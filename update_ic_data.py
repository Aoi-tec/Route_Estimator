"""IC座標データと公式ICマスターの再取得。料金検索は実施しない。"""
from pathlib import Path
import argparse
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from route_estimator.highway_service import HighwayService
from nexco_toll import TollError


def main():
    parser=argparse.ArgumentParser(description='ICデータの取得・検証（国土数値情報2025年現況 + NEXCO現行版）')
    parser.add_argument('--force',action='store_true',help='保存済み原本を再取得（年度は2025年に固定）')
    args=parser.parse_args()
    service=HighwayService(ROOT/'state')
    try:
        points=service.catalog.load(force=args.force)
        master=service.tolls.updater.load(force=args.force)
        print(f'ICデータ準備OK：座標 {len(points)}地点（2025-12-31基準） / NEXCO {master.version} {len(master.ics)}件')
        print('ICデータ版は2025年に固定。新年度を採用する際は元データの仕様・利用条件を確認してください。')
        return 0
    except (TollError,OSError,UnicodeError) as exc:
        print(f'ICデータは未更新：{exc}')
        return 2
    finally:service.close()


if __name__=='__main__':raise SystemExit(main())
