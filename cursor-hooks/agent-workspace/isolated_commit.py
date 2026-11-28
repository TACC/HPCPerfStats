#!/usr/bin/env python3
"""git isolated-commit: commit chosen paths without touching the shared working tree.

Why: pre-commit stashes *every* unstaged change in the checkout while its hooks run and puts it
back afterwards. When several agents/editors share one checkout, an edit saved during that
window can conflict with the restore and be rolled back or lost. This helper never stashes,
checks out or resets files in the shared checkout.

How:
  1. Snapshot: a throwaway index (GIT_INDEX_FILE) = HEAD + the named paths read from the working
     tree. The shared index and files are only read.
  2. Commit in a private slot worktree (<parent>/.<repo>-commit-<N>, a detached worktree that sits
     next to the checkout so hooks resolving `../.venv`, `../.build` keep working). The real
     `git commit` and its hooks run there; nothing of anyone else is unstaged there, so pre-commit
     has nothing to stash. Slots are reused (flock), so builds/caches stay warm.
     Hook auto-fixes (ruff --fix/format) are copied back to the shared file only if that file
     still matches the snapshot, then the commit is retried once.
  3. Publish: under the session git lock (recall Valkey, when run inside Claude Code) the branch is
     advanced with a compare-and-swap `update-ref`. If another commit landed meanwhile and touched
     none of our paths, our change is replayed onto it (no hook re-run); overlapping paths abort.
  4. The shared index entries of exactly those paths are refreshed to the new HEAD
     (`git reset -q -- <paths>`, index only). Other staged/unstaged work is untouched.

Usage: git isolated-commit -m MSG [-m MSG2] [-F FILE] [--dry-run] -- PATH...
Config (git config, optional):
  isolated-commit.link   repeatable; checkout-relative path symlinked from the shared checkout into
                         each slot when missing there (e.g. hpcperfstats/site/frontend/node_modules)
  isolated-commit.slots  number of slots (default 3)
Exit: 0 committed (or dry-run passed), 1 failure (branch untouched unless stated), 2 usage.
"""
import argparse
import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
SLOT_WAIT_S = 30 * 60
LOCK_WAIT_S = 10 * 60


class Fail(Exception):
    pass


def git(*args, cwd, env=None, check=True, capture=True, stdin=None):
    r = subprocess.run(["git", *args], cwd=cwd, env=env, text=True, input=stdin,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        raise Fail(f"git {' '.join(args)} failed ({r.returncode}): {(r.stderr or '').strip()}")
    return r if not check else (r.stdout or "").strip() if capture else r


def say(msg):
    print(f"isolated-commit: {msg}", file=sys.stderr, flush=True)


def blob_of(top, rel):
    """Blob id of the shared working-tree file, or None when it does not exist."""
    p = os.path.join(top, rel)
    if not os.path.lexists(p):
        return None
    if os.path.islink(p):
        return git("hash-object", "--stdin", cwd=top, stdin=os.readlink(p))
    return git("hash-object", "--", rel, cwd=top)


def tree_entries(top, tree, paths):
    """{path: (mode, blob)} for paths present in tree."""
    out = {}
    if not paths:
        return out
    r = git("ls-tree", "-r", "-z", tree, "--", *paths, cwd=top, check=False)
    for rec in (r.stdout or "").split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, _typ, obj = meta.split()
        out[path] = (mode, obj)
    return out


def tmp_index_env(tmpdir, name):
    env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmpdir, name))
    return env


# ---------------------------------------------------------------- slots
def slot_paths(top, n):
    parent, base = os.path.dirname(top), os.path.basename(top)
    return [os.path.join(parent, f".{base}-commit-{i}") for i in range(n)]


@contextlib.contextmanager
def acquire_slot(top, common, n, wait_s=SLOT_WAIT_S):
    lockdir = os.path.join(common, "isolated-commit")
    os.makedirs(lockdir, exist_ok=True)
    paths = slot_paths(top, n)
    deadline, told = time.time() + wait_s, False
    while True:
        for i, path in enumerate(paths):
            fd = os.open(os.path.join(lockdir, f"slot-{i}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            try:
                yield path
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            return
        if time.time() > deadline:
            raise Fail(f"all {n} commit slots busy for {wait_s}s")
        if not told:
            say(f"all {n} commit slots busy; waiting")
            told = True
        time.sleep(1)


def prepare_slot(top, slot, base_commit):
    ok = os.path.isdir(slot) and git("rev-parse", "--show-toplevel", cwd=slot, check=False)
    if not (ok and ok.returncode == 0 and os.path.realpath(ok.stdout.strip()) == os.path.realpath(slot)):
        if os.path.exists(slot) and os.listdir(slot):
            raise Fail(f"{slot} exists and is not a worktree of {top}; remove it or set "
                       "isolated-commit.slots")
        git("worktree", "prune", cwd=top, check=False)
        say(f"creating commit slot {slot}")
        git("worktree", "add", "-q", "--detach", slot, base_commit, cwd=top)
    git("checkout", "-q", "-f", "--detach", base_commit, cwd=slot)
    git("clean", "-fdq", cwd=slot)  # untracked leftovers only; ignored build dirs/caches stay
    links = git("config", "--get-all", "isolated-commit.link", cwd=top, check=False)
    for rel in (links.stdout or "").split():
        src, dst = os.path.join(top, rel), os.path.join(slot, rel)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)
        elif os.path.isdir(src) and os.path.isdir(dst) and not os.path.islink(dst):
            link_ignored_entries(slot, rel, src, dst)


def link_ignored_entries(slot, rel, src, dst):
    """The dir exists in the slot because it holds tracked files (e.g. generated/.gitignore), so
    link its missing entries instead, but only ones git ignores there: a tracked path absent from
    the slot's commit was deleted by that commit and must stay absent."""
    missing = [e for e in sorted(os.listdir(src)) if not os.path.lexists(os.path.join(dst, e))]
    if not missing:
        return
    r = git("check-ignore", "-z", "--stdin", cwd=slot, check=False,
            stdin="\0".join(os.path.join(rel, e) for e in missing) + "\0")
    for p in (r.stdout or "").split("\0"):
        if p:
            name = os.path.basename(p)
            os.symlink(os.path.join(src, name), os.path.join(dst, name))


# ---------------------------------------------------------------- session git lock
@contextlib.contextmanager
def session_git_lock(top, wait_s=LOCK_WAIT_S):
    """Hold the recall `git:<top>` lock (when run inside a Claude Code session) while publishing.
    Without Valkey or outside Claude this is a no-op; the compare-and-swap still protects HEAD."""
    try:
        sys.path.insert(0, HERE)
        import session_presence as sp
        v = sp.Valkey()
    except Exception:
        yield
        return
    try:
        pid = sp.find_claude_pid()
        sid = sp.sid_for_pid(v, pid) if pid else None
        if not sid:
            yield
            return
        st, me = sp.store(v), sp.owner(v, sid)
        res, call = f"git:{top}", f"isolated-commit-{os.getpid()}"
        deadline, told = time.time() + wait_s, False
        while True:
            ok, rec = st.acquire(res, me, "auto", 300, reason="git isolated-commit", call=call,
                                 cmd="git isolated-commit (publish)")
            if ok:
                break
            if time.time() > deadline:
                raise Fail(f"{res} held by session {(rec.get('sid') or '?')[:8]} for {wait_s}s; "
                           "nothing was published")
            if not told:
                say(f"waiting for {res} (held by session {(rec.get('sid') or '?')[:8]})")
                told = True
            time.sleep(2)
        try:
            yield
        finally:
            st.release(res, sid, call=call)
    finally:
        v.close()


# ---------------------------------------------------------------- steps
def snapshot(top, base, paths, tmpdir):
    env = tmp_index_env(tmpdir, "snap.index")
    git("read-tree", base, cwd=top, env=env)
    git("add", "-A", "--", *paths, cwd=top, env=env)
    tree = git("write-tree", cwd=top, env=env)
    changed = [p for p in git("diff-tree", "-r", "-z", "--name-only", "--no-renames", base, tree,
                              cwd=top).split("\0") if p]
    return tree, changed


def commit_in_slot(top, slot, msgfile, changed, snap_blobs, extra_args, copy_back=True):
    for attempt in (1, 2):
        r = subprocess.run(["git", "commit", "-q", "-F", msgfile, *extra_args], cwd=slot)
        if r.returncode == 0:
            return git("rev-parse", "HEAD", cwd=slot)
        fixed = [p for p in git("diff", "--name-only", "-z", cwd=slot).split("\0") if p]
        if not fixed:
            raise Fail(f"commit hooks failed (exit {r.returncode}); nothing was published. "
                       f"Inspect the slot: {slot}")
        outside = [p for p in fixed if p not in snap_blobs]
        if outside:
            raise Fail(f"hooks modified files outside this commit: {', '.join(outside)}; "
                       "nothing was published")
        if attempt == 2:
            raise Fail(f"hooks still modifying files after one retry: {', '.join(fixed)}")
        if not copy_back:  # dry run: never write the shared checkout; retry with fixes in slot
            say(f"hooks auto-fixed {', '.join(fixed)} (dry run: fixes kept in {slot} only)")
            git("add", "--", *fixed, cwd=slot)
            continue
        for p in fixed:
            if blob_of(top, p) != snap_blobs[p]:
                raise Fail(f"{p} changed in the shared checkout while hooks ran; its hook fixes "
                           f"are left in {slot}. Nothing was published; rerun.")
        for p in fixed:
            src, dst = os.path.join(slot, p), os.path.join(top, p)
            mode = os.stat(dst).st_mode
            shutil.copyfile(src, dst)
            os.chmod(dst, mode)
            snap_blobs[p] = blob_of(top, p)
        say(f"hooks auto-fixed {', '.join(fixed)}; copied fixes back and retrying")
        git("add", "--", *fixed, cwd=slot)
    raise Fail("unreachable")


def replay(top, new, onto, changed, tmpdir):
    """Recreate commit `new` (whose changes are limited to `changed`) on top of `onto`."""
    env = tmp_index_env(tmpdir, "replay.index")
    git("read-tree", onto, cwd=top, env=env)
    ents = tree_entries(top, new, changed)
    info = "".join(f"{m} {o}\t{p}\n" for p, (m, o) in ents.items())
    if info:
        git("update-index", "--index-info", cwd=top, env=env, stdin=info)
    gone = [p for p in changed if p not in ents]
    if gone:
        git("update-index", "--force-remove", "--", *gone, cwd=top, env=env)
    tree = git("write-tree", cwd=top, env=env)
    an, ae, ad = git("log", "-1", "--format=%an%x00%ae%x00%aD", new, cwd=top).split("\0")
    msg = git("cat-file", "commit", new, cwd=top, capture=True).split("\n\n", 1)[1]
    cenv = dict(os.environ, GIT_AUTHOR_NAME=an, GIT_AUTHOR_EMAIL=ae, GIT_AUTHOR_DATE=ad)
    r = subprocess.run(["git", "commit-tree", tree, "-p", onto], cwd=top, env=cenv, text=True,
                       input=msg, capture_output=True)
    if r.returncode != 0:
        raise Fail(f"commit-tree failed: {r.stderr.strip()}")
    return r.stdout.strip()


def publish(top, head_ref, base, new, changed, subject, tmpdir, lock_wait):
    with session_git_lock(top, lock_wait):
        for _ in range(5):
            if git("symbolic-ref", "-q", "HEAD", cwd=top, check=False).stdout.strip() != head_ref:
                raise Fail(f"HEAD no longer points at {head_ref}; commit {new[:12]} not published")
            cur = git("rev-parse", "HEAD", cwd=top)
            target = new
            if cur != base:
                overlap = [p for p in git("diff", "--name-only", "-z", "--no-renames", base, cur,
                                          "--", *changed, cwd=top).split("\0") if p]
                if overlap:
                    raise Fail(f"HEAD moved and the new commits also change {', '.join(overlap)}; "
                               f"commit {new[:12]} (on {base[:12]}) not published")
                target = replay(top, new, cur, changed, tmpdir)
                say(f"HEAD moved to {cur[:12]}; replayed onto it")
            r = git("update-ref", "-m", f"commit (isolated): {subject}", "HEAD", target, cur,
                    cwd=top, check=False)
            if r.returncode == 0:
                break
            base, new = cur, target  # raced again: retry from the commit we just built
        else:
            raise Fail("HEAD kept moving; nothing was published")
        for i in range(50):  # index-only refresh of our paths; another git may hold index.lock
            r = git("reset", "-q", "--", *changed, cwd=top, check=False)
            if r.returncode == 0:
                break
            time.sleep(0.2)
        else:
            say(f"WARNING: committed {target[:12]} but could not refresh the index for "
                f"{', '.join(changed)}: {r.stderr.strip()}. Run: git reset -q -- <those paths>")
    return target


def run(argv=None):
    ap = argparse.ArgumentParser(prog="git isolated-commit", description=__doc__.split("\n")[0])
    ap.add_argument("-m", "--message", action="append", default=[])
    ap.add_argument("-F", "--file")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the commit and its hooks in a slot but do not publish")
    ap.add_argument("--slots", type=int)
    ap.add_argument("--lock-wait", type=float, default=LOCK_WAIT_S)
    ap.add_argument("--slot-wait", type=float, default=SLOT_WAIT_S)
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    if bool(a.message) == bool(a.file):
        ap.error("give exactly one of -m (repeatable) or -F")

    cwd = os.getcwd()
    top = os.path.realpath(git("rev-parse", "--show-toplevel", cwd=cwd))
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=top)
    head_ref = git("symbolic-ref", "-q", "HEAD", cwd=top, check=False).stdout.strip()
    if not head_ref:
        raise Fail("HEAD is detached; isolated-commit needs a checked-out branch")
    rels = []
    for p in a.paths:
        ab = os.path.normpath(os.path.join(cwd, p))
        rel = os.path.relpath(ab, top)
        if rel == ".." or rel.startswith("../"):
            raise Fail(f"{p} is outside {top}")
        if rel == ".":
            raise Fail("refusing to commit the whole tree; name the paths")
        rels.append(rel)
    n = a.slots or int((git("config", "--get", "isolated-commit.slots", cwd=top, check=False)
                        .stdout or "3").strip() or 3)

    with tempfile.TemporaryDirectory(prefix="isolated-commit-") as tmpdir:
        msgfile = os.path.join(tmpdir, "MSG")
        if a.file:
            shutil.copyfile(a.file if os.path.isabs(a.file) else os.path.join(cwd, a.file), msgfile)
        else:
            with open(msgfile, "w") as f:
                f.write("\n\n".join(a.message) + "\n")
        with open(msgfile) as f:
            subject = next((ln.strip() for ln in f if ln.strip()), "")

        base = git("rev-parse", "HEAD", cwd=top)
        tree, changed = snapshot(top, base, rels, tmpdir)
        if not changed:
            raise Fail("nothing to commit: the named paths match HEAD")
        snap = tree_entries(top, tree, changed)
        snap_blobs = {p: (snap[p][1] if p in snap else None) for p in changed}
        say(f"{len(changed)} path(s) on {base[:12]}: {', '.join(changed)}")

        with acquire_slot(top, common, n, a.slot_wait) as slot:
            prepare_slot(top, slot, base)
            git("read-tree", "--reset", "-u", tree, cwd=slot)
            new = commit_in_slot(top, slot, msgfile, changed, snap_blobs, [],
                                 copy_back=not a.dry_run)
            if a.dry_run:
                say(f"dry run OK: hooks passed, commit {new[:12]} built in {slot}; not published")
                return 0
            target = publish(top, head_ref, base, new, changed, subject, tmpdir, a.lock_wait)
    print(git("log", "-1", "--stat", "--format=%h %s", target, cwd=top))
    return 0


def main():
    try:
        sys.exit(run())
    except Fail as e:
        say(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
