"""APIキーを端末の非表示入力で登録する。引数やログにキーを含めない。"""
import getpass
import os
from pathlib import Path
import sys
import warnings

from route_estimator.config import load_config
from route_estimator.credentials import write_key_file
from route_estimator.errors import EstimatorError
from route_estimator.routing.geoapify_client import validate_key

ROOT=Path(__file__).resolve().parent


def save_key(path,key,env_name='GEOAPIFY_API_KEY'):
    validate_key(key)
    write_key_file(path,env_name,key)


def main():
    try:
        settings=load_config(ROOT/'config.json')
        print('Geoapify MyProjectsで作成したAPIキーを入力してください（画面には表示されません）。')
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            key=getpass.getpass('Geoapify API key: ').strip()
        save_key(settings.routing.api_key_file,key,settings.routing.api_key_env)
        print(f'APIキーを {settings.routing.api_key_file.name} に登録しました（Git対象外）。startで起動できます。')
        if settings.routing.api_key_env in os.environ:
            print('環境変数のキーが優先されます。古い環境変数がある場合は更新してください。')
        return 0
    except (KeyboardInterrupt,EOFError):return 130
    except getpass.GetPassWarning:
        print('非表示入力ができる端末から起動してください。',file=sys.stderr);return 2
    except (EstimatorError,OSError):
        print('APIキーを登録できません。設定・キー形式・保存先を確認してください。',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
