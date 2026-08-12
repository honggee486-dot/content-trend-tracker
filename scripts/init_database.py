from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DEFAULT_DB_PATH
from src.database import init_database


if __name__ == "__main__":
    init_database(DEFAULT_DB_PATH)
    print(f"[OK] 메인 DuckDB 초기화 완료: {DEFAULT_DB_PATH}")
