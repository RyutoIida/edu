import subprocess, sys

def run(cmd):
    print(f"$ {cmd}")
    res = subprocess.run(cmd, shell=True)
    if res.returncode != 0:
        sys.exit(res.returncode)

if __name__ == "__main__":
    run("python classify_msg.py")
    run("python normalize.py")
    run("python gen_req.py")