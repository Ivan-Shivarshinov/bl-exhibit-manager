"""PyInstaller entry point; source execution uses python -m exhibit.launch."""
import sys
from exhibit.launch import main

if __name__=="__main__":
    sys.exit(main())
