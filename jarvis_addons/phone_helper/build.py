"""Builds jarvis_link.apk (the tiny JARVIS Link helper for the phone) from the sources in this folder.

    venv\\Scripts\\python.exe jarvis_addons\\phone_helper\\build.py SDK_DIR [JAVA_HOME]

SDK_DIR must hold Android's build tools (aapt2.exe, zipalign.exe, lib\\d8.jar, lib\\apksigner.jar) and android.jar, anywhere below it.
The finished jarvis_link.apk in this folder is what `phone_link --helper` installs; rebuilding is only needed after changing the
sources. The signing key (jarvis_link.keystore, made on the first build) stays on this PC: an update must be signed with the same key."""
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def find(root, name):
    hits = glob.glob(os.path.join(root, "**", name), recursive=True)
    if not hits:
        raise SystemExit(f"{name} not found below {root}")
    return hits[0]


def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED: {' '.join(args[:3])} ...\n{r.stdout}{r.stderr}")
    return r.stdout


def keystore_secret():
    """The signing key's password: kept in jarvis_link.keystore.pass next to the key (neither file is published), or in the
    environment variable JARVIS_LINK_KEYSTORE_PASS. Made at random the first time."""
    path = os.path.join(HERE, "jarvis_link.keystore.pass")
    if os.environ.get("JARVIS_LINK_KEYSTORE_PASS"):
        return os.environ["JARVIS_LINK_KEYSTORE_PASS"]
    if not os.path.exists(path):
        import secrets
        with open(path, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(18))
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def main(sdk, java_home=None):
    jbin = os.path.join(java_home, "bin") if java_home else ""
    java, javac, keytool = (os.path.join(jbin, x) if jbin else x for x in ("java", "javac", "keytool"))
    jar, aapt2, zipalign = find(sdk, "android.jar"), find(sdk, "aapt2.exe"), find(sdk, "zipalign.exe")
    d8, signer = find(sdk, "d8.jar"), find(sdk, "apksigner.jar")
    out = os.path.join(HERE, "_build")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "classes"))
    srcs = glob.glob(os.path.join(HERE, "src", "**", "*.java"), recursive=True)
    run(javac, "-source", "8", "-target", "8", "-Xlint:-options", "-bootclasspath", jar, "-d", os.path.join(out, "classes"), *srcs)
    classes = glob.glob(os.path.join(out, "classes", "**", "*.class"), recursive=True)
    run(java, "-cp", d8, "com.android.tools.r8.D8", "--min-api", "30", "--lib", jar, "--output", out, *classes)
    base = os.path.join(out, "base.apk")
    run(aapt2, "link", "-o", base, "-I", jar, "--manifest", os.path.join(HERE, "AndroidManifest.xml"))
    with zipfile.ZipFile(base, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(out, "classes.dex"), "classes.dex")
    aligned = os.path.join(out, "aligned.apk")
    run(zipalign, "-f", "4", base, aligned)
    ks = os.path.join(HERE, "jarvis_link.keystore")
    secret = keystore_secret()
    if not os.path.exists(ks):
        run(keytool, "-genkeypair", "-keystore", ks, "-storepass", secret, "-keypass", secret, "-alias", "jarvis", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "20000", "-dname", "CN=JARVIS Link")
    apk = os.path.join(HERE, "jarvis_link.apk")
    run(java, "-jar", signer, "sign", "--ks", ks, "--ks-pass", "pass:" + secret, "--key-pass", "pass:" + secret, "--out", apk, aligned)
    shutil.rmtree(out, ignore_errors=True)
    for junk in glob.glob(apk + ".idsig"):
        os.remove(junk)
    print(f"built {apk} ({os.path.getsize(apk)} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(*sys.argv[1:3])
