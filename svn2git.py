import os
import re
import json
import time
import shlex
import argparse
import requests
import tempfile
import subprocess
import multiprocessing

# token to access github api
TOKEN = ''

def get_lastest_tag(tagurl='.'):
    cmd = 'git ls-remote --tags %s' %tagurl
    output = subprocess.check_output(shlex.split(cmd)).decode('utf-8')
    tags = [l.split('\t')[1] for l in output.splitlines()]
    r = re.compile('.*?/(V\d\d-\d\d-\d\d)')
    prod_tags = []
    for tag in tags:
        match = re.search(r, tag)
        if match:
            prod_tags.append(match.group(1))
    prod_tags.sort()
    if len(prod_tags) == 0:
        print('Could not find any production tag')
        print(tagurl)
        return None
    return prod_tags[-1]

def svn_authors(url, file_name):
    cmd  = '''svn log %s \
            --xml --quiet | grep author | sort -u ''' %url

    authors = subprocess.check_output(cmd, shell=True).decode('utf-8').splitlines()
    with open(file_name, 'w') as fh:
        for author in authors:
            author = re.search('.*>(.*?)<.*', author).group(1)
            ind = author.find('@')
            if ind > 0:
                name = author[:ind]
                email = author
            else:
                name = author
                email = 'none@example.com'
            fh.write('%s = %s <%s>\n' %(author, name, email))
        fh.write('(no author) = no author <no_author@nowhere.com>')

def convert_repo(url):
    if not os.path.isdir('repos'):
        os.mkdir('repos')
    repo = url[url.rfind('/')+1:]
    dest = os.path.join('repos', repo)
    if os.path.isdir(dest):
        print('Destination directory already exists')
        print('Skipping repo %s' %repo)
        return False

    print('%s Start converting %s' %(time.ctime(), repo))
    _, author_file = tempfile.mkstemp('.txt')
    svn_authors(url, author_file)
    cmd = '''git svn clone %s \
                     --stdlayout --no-metadata \
                     --authors-file=%s --prefix "" %s''' %(url, author_file, dest)
    _ = subprocess.check_output(shlex.split(cmd))
    current_dir = os.getcwd()
    os.chdir(dest)

    # convert svn tag branches to git tags
    cmd = '''for t in $(git for-each-ref --format='%(refname:short)' refs/remotes/tags); do \
              git tag ${t/tags\//} $t && git branch -D -r $t; done'''
    _ = subprocess.check_output(cmd, shell=True)

    # convert svn branches to git branches
    cmd = '''for b in $(git for-each-ref --format='%(refname:short)' refs/remotes); do \
        git branch $b refs/remotes/$b && git branch -D -r $b; done'''
    _ = subprocess.check_output(cmd, shell=True)

    # clean up tags
    cmd = '''for p in $(git for-each-ref --format='%(refname:short)' | grep @);
             do git tag -d $p; done'''
    subprocess.call(cmd, shell=True)

    cmd = 'git branch -d trunk'
    _ = subprocess.check_output(shlex.split(cmd))

    # if conda branch exists merge it pack into master and tag release
    cmd = 'git branch --list conda'
    if subprocess.check_output(shlex.split(cmd)):
        merge_conda_branch(repo)

    os.chdir(current_dir)
    os.remove(author_file)
    print('%s Finished converting %s' %(time.ctime(), repo))
    return True

def merge_conda_branch(repo):
    print('Merging conda branch into master')
    cmd = 'git merge --strategy=recursive -X theirs conda -m "Merge conda branch into master"'
    subprocess.check_call(shlex.split(cmd))

    tag = get_lastest_tag()
    if tag:
        version = '%s%02d' %(tag[:-2], int(tag[-2:]) + 1)
    else:
        version = 'V00-00-01'
    print(version)
    cmd = 'git tag -a %s -m "%s"' %(version, version)
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git branch -d conda'
    subprocess.check_call(shlex.split(cmd))

def push_repo(repo):
    dest = os.path.join('repos', repo)
    current_dir = os.getcwd()
    os.chdir(dest)
    cmd = 'git remote add origin git@github.com:lcls-psana/%s.git' %repo
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git push --force origin --all'
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git push --force origin --tags'
    subprocess.check_call(shlex.split(cmd))
    os.chdir(current_dir)

def create_repo(repo):
    data = {'name': repo, 'description': ''}
    r = requests.post('https://api.github.com/orgs/lcls-psana/repos',
                      data=json.dumps(data), auth=('weninc', TOKEN))
    if r.status_code != 201:
        print('Error in creating repo %s' %repo)

def delete_repo(repo):
    r = requests.delete('https://api.github.com/repos/lcls-psana/%s' %repo,
                        auth=('weninc', TOKEN))
    if r.status_code != 204:
        print('Error in deleting repo %s' %repo)

def prepare_repos_merge(repo):
    current_dir = os.getcwd()
    os.chdir(os.path.join('repos', repo))
    os.mkdir(repo)
    subprocess.call('mv * %s' %repo, shell=True)

    cmd = 'git add %s' %repo
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git commit -a -m "Preparing %s for move"' %repo
    subprocess.check_call(shlex.split(cmd))
    os.chdir(current_dir)

def merge_repos(source, dest):
    prepare_repos_merge(source)
    current_dir = os.getcwd()
    os.chdir(os.path.join('repos', dest))

    cmd = 'git remote add temp %s' %os.path.join('../', source)
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git fetch temp'
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git merge --allow-unrelated-histories temp/master -m "merge %s repo into current repo"' %source
    subprocess.check_call(shlex.split(cmd))

    cmd = 'git remote rm temp'
    subprocess.check_call(shlex.split(cmd))
    os.chdir(current_dir)

with open('repos2.txt', 'r') as fh:
    repos = fh.read().splitlines()

def run(repo):
    if convert_repo('https://pswww.slac.stanford.edu/svn-readonly/psdmrepo/', repo):
        create_repo(repo)
        push_repo(repo)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Covert svn repositories to git')
    parser.add_argument('-u','--url', help='url for svn repository', required=True, type=str)
    parser.add_argument('--psdm', help='convert psdm repositories', required=False, type=bool)
    args = parser.parse_args()
    if args.psdm:
        pool = multiprocessing.Pool()
        pool.map(run, repos)
    else:
        convert_repo(args.url)
