"""Wrapper autour d'uvicorn qui garantit le nettoyage des processus enfants
(notamment le worker --reload) quand le processus parent est tué (Ctrl+C)."""
import os
import sys
import signal
import subprocess
import atexit

CHILD_PIDS: set[int] = set()


def _kill_children():
    """Tue tous les processus enfants connus, puis force le nettoyage du port."""
    import signal as _sig
    for pid in list(CHILD_PIDS):
        try:
            os.kill(pid, _sig.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
    # Laisser un court délai pour l'arrêt propre, puis forcer
    import time
    time.sleep(0.5)
    for pid in list(CHILD_PIDS):
        try:
            os.kill(pid, _sig.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    CHILD_PIDS.clear()


def _cleanup_port(port: int):
    """Tue tout processus à l'écoute sur le port donné (Windows uniquement)."""
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid_str = parts[-1]
                if pid_str.isdigit():
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid_str],
                        capture_output=True,
                    )
    except Exception:
        pass


atexit.register(_kill_children)

# ── Gestion des signaux ───────────────────────────────────────────────────
def _on_signal(signum, frame):
    _kill_children()
    sys.exit(0)


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

    # Nettoyage préalable du port
    _cleanup_port(port)

    # Lancement d'uvicorn en tant que sous-processus
    uvicorn_args = [
        sys.executable, "-m", "uvicorn",
        "app:app",
        "--app-dir", "backend",
        "--reload",
        "--port", str(port),
    ]

    proc = subprocess.Popen(
        uvicorn_args,
        # Hériter des flux stdout/stderr du parent — évite les pipes
        # et les erreurs d'encodage cp1252/utf-8 sur Windows.
        stdout=sys.stdout,
        stderr=sys.stderr,
        stdin=subprocess.DEVNULL,
    )
    CHILD_PIDS.add(proc.pid)

    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        # Tuer l'arbre de processus uvicorn
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        _kill_children()
        _cleanup_port(port)
