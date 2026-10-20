"""近畿デモ2地点でローカルOSRMの準備完了を最大45秒待つ。"""
import argparse
from pathlib import Path
import time
import httpx
from route_estimator.config import load_config

parser = argparse.ArgumentParser()
parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'config.json')
args = parser.parse_args()
settings = load_config(args.config)
url = settings.osrm.base_url.rstrip('/') + '/route/v1/' + settings.osrm.profile + '/135.501276687,34.693870178;135.504533435,34.693508109?overview=false&steps=false'
end = time.monotonic() + 45
with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as client:
    while time.monotonic() < end:
        try:
            response = client.get(url)
            if response.status_code == 200 and response.json().get('code') == 'Ok':
                print('近畿OSRMの準備完了（公開2地点の経路応答を確認）')
                raise SystemExit(0)
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        time.sleep(0.5)
raise SystemExit('OSRMが準備完了になりません。Composeの状態・地図・ログを確認してください')
