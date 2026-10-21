"""
Start de AHN profielen webapp direct vanuit de repository (installeren is niet nodig).

    python app.py --config config.ini
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ahnprofielcreator.app import main  # noqa: E402

sys.exit(main())
