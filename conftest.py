"""
conftest.py — pytest 루트 설정

이 파일이 있어야 pytest가 프로젝트 루트를 sys.path에 추가하여
`from strategy.cost_guard import CostGuard` 같은 임포트가 가능해집니다.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
