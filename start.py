"""Start the local server detached from Terminal and optionally open the dashboard."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE=Path(__file__).resolve().parent


def running(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
            return json.load(response).get('app')=='debate-blocks'
    except (OSError,ValueError,urllib.error.URLError):return False


def main(open_browser=False):
    if not running(8420):
        with (BASE/'dashboard.log').open('ab') as log:
            subprocess.Popen([sys.executable,'-u',str(BASE/'server.py')],cwd=BASE,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                start_new_session=True,close_fds=True,
                creationflags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if running(8420):break
            time.sleep(.2)
    if not running(8420):
        raise SystemExit(f'The app could not start on port 8420. See {BASE / "dashboard.log"}.')
    print('The app is running at http://localhost:8420')
    if open_browser:
        import webbrowser
        webbrowser.open('http://localhost:8420')


if __name__=='__main__':main('--open' in sys.argv)
