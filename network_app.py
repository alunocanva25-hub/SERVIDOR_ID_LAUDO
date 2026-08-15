"""Servidor de rede do ID LAUDO V1.0.0.28."""
from __future__ import annotations

import socket
import uvicorn

HOST = "0.0.0.0"
PORT = 8872


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "0.0.0.0"
    finally:
        s.close()


if __name__ == "__main__":
    ip = local_ip()
    print("=" * 68)
    print(" ID LAUDO V1.0.0.28 - SERVIDOR PARA CELULAR / TABLET")
    print("=" * 68)
    print("Conecte o celular/tablet na MESMA REDE deste computador.")
    print()
    print("IP PARA DIGITAR NO APP:")
    print(f"  {ip}")
    print()
    print(f"O app completa automaticamente: http://{ip}:{PORT}/")
    print("Voce NAO precisa digitar http, pontos nem a porta.")
    print()
    print("Para encerrar, feche esta janela ou pressione CTRL+C.")
    print("=" * 68)
    uvicorn.run("main:app", host=HOST, port=PORT, log_level="warning")
