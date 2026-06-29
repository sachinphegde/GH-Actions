import os
import json
import urllib.request
import urllib.error

TOKEN = os.environ["GH_TOKEN"]
PARENT_REPO = os.environ["PARENT_REPO"]
# Expects a comma-separated string, e.g., "libs/auth,libs/logger"
SUBMODULE_LIST = [s.strip() for s in os.environ["SUBMODULE_LIST"].split(",") if s.strip()]
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json"
}

def api_request(url, method="GET", data=None):
    req = urllib.request.Request(url, headers=headers, method=method)
    if data:
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"API Error on {url}: {e.code} - {e.read().decode('utf-8')}")
        raise e

def main():
    if not SUBMODULE_LIST:
        print("No submodules provided to update.")
        return

    print(f"Fetching base branch info from parent: {PARENT_REPO}")
    branch_info = api_request(f"https://api.github.com/repos/{PARENT_REPO}/branches/{BASE_BRANCH}")
    parent_commit_sha = branch_info["commit"]["sha"]
    base_tree_sha = branch_info["commit"]["commit"]["tree"]["sha"]

    # This array will hold all the submodule updates to commit at once
    tree_updates = []
    updated_names = []

    # Parse and fetch tracking SHAs for each requested submodule
    for sub_path in SUBMODULE_LIST:
        print(f"\n--- Processing Submodule: {sub_path} ---")
        
        # 1. Look up where this submodule points by querying the parent repo's configuration
        # Alternatively, if your submodule repo name matches the folder name, you can optimize this.
        # For safety, we fetch the submodule mapping directly from GitHub API
        try:
            sub_meta = api_request(f"https://api.github.com/repos/{PARENT_REPO}/contents/{sub_path}?ref={BASE_BRANCH}")
            # Submodules return a type 'submodule' and a 'submodule_git_url'
            if sub_meta.get("type") != "submodule":
                print(f"Warning: {sub_path} is not recognized as a submodule by GitHub. Skipping.")
                continue
                
            # Extract the submodule repo name from its git URL (e.g., "git@github.com:org/repo.git")
            git_url = sub_meta["submodule_git_url"]
            sub_repo = git_url.split("github.com/")[-1].replace(".git", "").replace(":", "/")
        except Exception:
            print(f"Could not automatically resolve submodule repository for {sub_path}. Skipping.")
            continue

        # 2. Get the latest commit SHA from that submodule repo
        print(f"Fetching latest commit for remote repo: {sub_repo}")
        try:
            sub_commit = api_request(f"https://api.github.com/repos/{sub_repo}/commits/{BASE_BRANCH}")
            sub_sha = sub_commit["sha"]
            print(f"Target SHA resolved: {sub_sha}")
            
            # Append this specific update into our Git Tree list
            tree_updates.append({
                "path": sub_path,
                "mode": "160000", # gitlink mode
                "type": "commit",
                "sha": sub_sha
            })
            updated_names.append(sub_path.split("/")[-1])
        except Exception:
            print(f"Failed to fetch remote SHA for {sub_repo}. Skipping.")

    if not tree_updates:
        print("No valid submodule updates found. Exiting.")
        return

    # 3. Create a single new tree object containing ALL the submodule updates combined
    print("\nCreating bulk write tree object via API...")
    tree_data = {
        "base_tree": base_tree_sha,
        "tree": tree_updates
    }
    new_tree = api_request(f"https://api.github.com/repos/{PARENT_REPO}/git/trees", "POST", tree_data)
    new_tree_sha = new_tree["sha"]

    # 4. Create one single commit for all updates
    print("Creating single batch commit via API...")
    summary_message = f"chore: bulk update submodules ({', '.join(updated_names)})"
    commit_data = {
        "message": summary_message,
        "tree": new_tree_sha,
        "parents": [parent_commit_sha]
    }
    new_commit = api_request(f"https://api.github.com/repos/{PARENT_REPO}/git/commits", "POST", commit_data)
    new_commit_sha = new_commit["sha"]

    # 5. Push to a branch unique to this batch
    branch_name = f"bulk-submodule-update-{new_commit_sha[:7]}"
    print(f"Creating new branch: {branch_name}")
    ref_data = {
        "ref": f"refs/heads/{branch_name}",
        "sha": new_commit_sha
    }
    api_request(f"https://api.github.com/repos/{PARENT_REPO}/git/refs", "POST", ref_data)

    # 6. Open one single Pull Request
    print("Opening Pull Request...")
    pr_data = {
        "title": f"chore: automated update of {len(tree_updates)} submodules",
        "head": branch_name,
        "base": BASE_BRANCH,
        "body": f"Automated batch update for the following submodules:\n" + \
                "\n".join([f"- `{u['path']}` to `{u['sha'][:7]}`" for u in tree_updates])
    }
    pr_result = api_request(f"https://api.github.com/repos/{PARENT_REPO}/pulls", "POST", pr_data)
    print(f"Successfully created a single consolidated PR: {pr_result['html_url']}")

if __name__ == "__main__":
    main()