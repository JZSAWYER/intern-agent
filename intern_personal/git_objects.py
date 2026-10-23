"""Bounded Git object reads and private proofs for unchanged validation rules."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .core import Invalid
from .live_state import private_json,write_private


class MissingObject(Invalid):pass


class Objects:
    def __init__(self,directory):
        self.process=subprocess.Popen(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(directory),
            'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env=os.environ|{'GIT_NO_LAZY_FETCH':'1'})
        self.cache=OrderedDict();self.bytes=0

    def get(self,spec):
        if spec in self.cache:
            value=self.cache.pop(spec);self.cache[spec]=value;return value
        if not isinstance(spec,str) or any(c in spec for c in '\0\r\n'):raise Invalid('Invalid Git object reference')
        try:
            self.process.stdin.write(spec.encode()+b'\n');self.process.stdin.flush()
            header=self.process.stdout.readline(4097).split()
            if len(header)==2 and header[1]==b'missing':raise MissingObject('Git object is unavailable')
            if len(header)!=3 or not re.fullmatch(b'[0-9a-f]{40,64}',header[0]) or not header[2].isdigit():raise Invalid('Git object is unavailable')
            size=int(header[2])
            if size>10*1024*1024:raise Invalid('Large unpublished blob requires upload review')
            data=self.process.stdout.read(size)
            if len(data)!=size or self.process.stdout.read(1)!=b'\n':raise Invalid('Git object read was incomplete')
        except (OSError,BrokenPipeError):raise Invalid('Git object reader is unavailable') from None
        value=(header[1],data)
        if size<=1024*1024:
            self.cache[spec]=value;self.bytes+=size
            while self.bytes>16*1024*1024 or len(self.cache)>256:
                _,old=self.cache.popitem(last=False);self.bytes-=len(old[1])
        return value

    def blob(self,spec):
        kind,data=self.get(spec)
        if kind!=b'blob':raise Invalid('Expected a regular Git blob')
        return data

    def close(self):
        try:self.process.stdin.close()
        except BrokenPipeError:pass
        self.process.stdout.close()
        try:self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class Proofs:
    """A cached tip is trusted only after this exact policy checked its history."""
    def __init__(self,config_path,config,base):
        self.path=Path(config_path).parent/'validation-cache.json'
        key=dict(directory=config['directory'],hashes=config['hashes'],base=base,
            branches=config['branches'],management=config['management'],combined=config.get('combined_records',False))
        self.policy=hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()
        data=private_json(self.path) if self.path.exists() or self.path.is_symlink() else {}
        self.tips=data.get('tips',{}) if data.get('policy')==self.policy else {}
        if not isinstance(self.tips,dict) or any(not isinstance(k,str) or not isinstance(v,str)
                or not re.fullmatch('[0-9a-f]{40,64}',v) for k,v in self.tips.items()):raise Invalid('Invalid Git validation proof')

    def start(self,directory,branch,tip,base):
        previous=self.tips.get(branch)
        if previous:
            check=subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(directory),
                'merge-base','--is-ancestor',previous,tip],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if check.returncode==0:return previous
        return base

    def save(self,branch,tip):
        self.tips[branch]=tip
        write_private(self.path,dict(schema='intern-agent.git-validation.v1',policy=self.policy,tips=self.tips))
