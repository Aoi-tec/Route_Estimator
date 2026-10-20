from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
import hashlib
import json
import os
import re
import shutil
from .master import ICMaster
from .models import FetchError, ParseError
from .scraper import HOST


class MasterUpdater:
    def __init__(self, fetcher, directory):
        self.fetcher=fetcher
        self.directory=Path(directory)

    def load(self, *, force=False):
        current=self.directory/'current.js'
        if current.is_file() and not force:
            try:
                info=json.loads((self.directory/'source.json').read_text())
                body=current.read_bytes()
                age=datetime.now(timezone.utc)-datetime.fromisoformat(info['retrieved_at'])
                if timedelta(0)<=age<timedelta(days=7) and hashlib.sha256(body).hexdigest()==info['sha256']:
                    return ICMaster(body.decode('utf-8'))
            except (OSError,ValueError,KeyError,TypeError,ParseError): pass
        home=self.fetcher.get(HOST+'/').decode('utf-8')
        candidates=re.findall(r'''src=["'](/js_navi/route_master_v[0-9]+\.js(?:\?[^"']*)?)["']''',home)
        if len(set(candidates))!=1: raise ParseError('現行IC原本のURLを一意に特定できません')
        url=urljoin(HOST,candidates[0])
        body=self.fetcher.get(url)
        master=ICMaster(body.decode('utf-8'))
        self.directory.mkdir(parents=True,exist_ok=True)
        temp=self.directory/'current.js.tmp'
        temp.write_bytes(body)
        if current.is_file(): shutil.copyfile(current,self.directory/'previous.js')
        os.replace(temp,current)
        info={'url':url,'version':master.version,'sha256':hashlib.sha256(body).hexdigest(),
              'retrieved_at':datetime.now(timezone.utc).isoformat(),'records':len(master.ics)}
        temp=self.directory/'source.json.tmp';temp.write_text(json.dumps(info,ensure_ascii=False,indent=2));os.replace(temp,self.directory/'source.json')
        return master
