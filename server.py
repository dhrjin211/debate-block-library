"""Run the app on http://localhost:8420 (or another port: server.py --port 8766)."""
import sys
from http.server import ThreadingHTTPServer

from dashboard import H


def main():
    port = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 8420
    server=ThreadingHTTPServer(('127.0.0.1',port),H)
    print(f'Listening on http://localhost:{port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
