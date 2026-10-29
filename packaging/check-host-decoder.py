#!/usr/bin/env python3
"""Check the real decoder protocol and shutdown without requiring GPU access.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decoder", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="waydroid-hwdec-check-") as directory:
        address = Path(directory) / "decoder.sock"
        with (Path(directory) / "decoder.log").open("w+") as log:
            process = subprocess.Popen([str(args.decoder.resolve()), str(address)], stderr=log)
            try:
                deadline = time.monotonic() + 5
                while not address.is_socket():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        log.seek(0)
                        raise RuntimeError("Decoder did not start: " + log.read())
                    time.sleep(0.01)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(3)
                    connection.connect(str(address))
                    for command in (1, 3, 4):
                        connection.sendall(struct.pack("<IIQII", 0x57484431, command, 0, 0, 0))
                        response = bytearray()
                        while len(response) < 56:
                            chunk = connection.recv(56 - len(response))
                            if not chunk:
                                raise RuntimeError("Decoder closed an incomplete reply")
                            response.extend(chunk)
                        if struct.unpack_from("<Ii", response) != (0x57484431, 0):
                            raise RuntimeError("Decoder protocol rejected command " + str(command))
                process.terminate()
                if process.wait(timeout=3) != 0:
                    raise RuntimeError("Decoder did not shut down cleanly")
                if address.exists():
                    raise RuntimeError("Decoder left its socket after shutdown")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    print("Host decoder: init, flush, close and clean shutdown passed")


if __name__ == "__main__":
    main()
