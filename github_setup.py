#!/usr/bin/env python3

import os
import json
import sys

from github import Github, GithubException


REPO_NAME = 'Manifest-Architect-Public'
VERSION_DATA = {
    'version': '1.0.0',
    'url': '',
    'changelog': 'Initial Release',
}


def get_github():
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print('ERROR: Set GITHUB_TOKEN environment variable first.')
        print('  export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx')
        sys.exit(1)
    return Github(token)


def create_repo(g):
    user = g.get_user()
    try:
        repo = user.create_repo(
            REPO_NAME,
            private=False,
            auto_init=True,
            description='Manifest Architect — Steam manifest management tool',
        )
        print(f'Created public repo: {repo.html_url}')
        return repo
    except GithubException as e:
        if e.status == 422 and 'already exists' in str(e):
            print(f'Repo {REPO_NAME} already exists — using it.')
            return user.get_repo(REPO_NAME)
        raise


def create_folder_structure(repo):
    paths = ['src/', 'builds/', 'metadata/']
    for p in paths:
        try:
            repo.create_file(
                f'{p}.gitkeep',
                f'Initialize {p.rstrip("/")} directory',
                '',
            )
            print(f'  Created {p}')
        except GithubException as e:
            if e.status == 422:
                print(f'  {p} already exists — skipped')
            else:
                raise


def upload_version_json(repo):
    content = json.dumps(VERSION_DATA, indent=2)
    try:
        repo.create_file(
            'metadata/version.json',
            'Add version metadata',
            content,
        )
        print('  Uploaded metadata/version.json')
    except GithubException as e:
        if e.status == 422:
            existing = repo.get_contents('metadata/version.json')
            repo.update_file(
                'metadata/version.json',
                'Update version metadata',
                content,
                existing.sha,
            )
            print('  Updated metadata/version.json')
        else:
            raise


def set_branch_protection(repo, owner_login):
    try:
        branch = repo.get_branch('main')
        branch.edit_protection(
            enforce_admins=False,
            required_linear_history=True,
        )
        print('  Set branch protection — enforce_admins=False, required_linear_history=True')

        print(f'\n  NOTE: For "only owner can push", go to:')
        print(f'    {repo.html_url}/settings/branches')
        print(f'  Add a rule for "main" and restrict push access to "{owner_login}".')
    except GithubException as e:
        print(f'  Warning: could not set branch protection: {e}')


def main():
    g = get_github()
    user = g.get_user()

    repo = create_repo(g)
    create_folder_structure(repo)
    upload_version_json(repo)
    set_branch_protection(repo, user.login)

    raw_url = f'https://raw.githubusercontent.com/{user.login}/{REPO_NAME}/main/metadata/version.json'
    print(f'\nDone. Version metadata will be served at:\n  {raw_url}')


if __name__ == '__main__':
    main()
