"""本人キー登録後に公開5経路を確認。実行するとAPIを最大5回呼ぶ。"""
import json
from pathlib import Path
from datetime import datetime,timezone
from route_estimator.bootstrap import open_adapters
from route_estimator.config import load_config
from route_estimator.models import Coordinates
from route_estimator.errors import EstimatorError

ROOT=Path(__file__).resolve().parent
CASES=[
    ('大阪',(135.501276687,34.693870178),(135.504533435,34.693508109)),
    ('東京',(139.7671,35.6812),(139.7684,35.6750)),
    ('札幌',(141.3508,43.0687),(141.3545,43.0618)),
    ('那覇',(127.6792,26.2124),(127.6859,26.2161)),
    ('東京→大阪',(139.7671,35.6812),(135.501276687,34.693870178)),
]

def main():
    record={'checked_at':datetime.now(timezone.utc).isoformat(),'routes':[],'success':False}
    try:
        settings=load_config(ROOT/'config.json')
        if settings.routing.provider!='geoapify':
            print('この確認はGeoapify設定専用です');return 2
        with open_adapters(settings) as (_,router):
            for name,a,b in CASES:
                result=router.route(Coordinates(a[1],a[0]),Coordinates(b[1],b[0]))
                item={'name':name,'distance_m':str(result.distance_m),'duration_s':str(result.duration_s),
                      'endpoint_distances_m':[str(x) for x in result.snap_distances_m],
                      'routing_metadata':result.routing_metadata}
                record['routes'].append(item)
                print(json.dumps(item,ensure_ascii=False))
        record['success']=True
        return 0
    except EstimatorError as exc:
        record['error']=str(exc);print('API確認未完了：'+str(exc));return 2
    except (KeyboardInterrupt,EOFError):
        record['error']='利用者中断';return 130
    finally:
        output=ROOT/'state/geoapify_smoke.json';output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':raise SystemExit(main())
