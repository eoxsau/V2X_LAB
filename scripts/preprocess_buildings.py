#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.buildings.building_preprocessor import preprocess_buildings


if __name__ == "__main__":
    result = preprocess_buildings()
    print(result)
