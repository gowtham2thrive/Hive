import subprocess
import collections

try:
    output = subprocess.check_output(['git', 'log', '--numstat', '--format=COMMIT:%aN'], text=True, encoding='utf-8')
except Exception as e:
    print('Error:', e)
    exit(1)

authors = collections.defaultdict(lambda: {'commits': 0, 'added': 0, 'deleted': 0})
current_author = None

for line in output.split('\n'):
    line = line.strip()
    if not line: continue
    if line.startswith('COMMIT:'):
        current_author = line.split(':', 1)[1].strip()
        authors[current_author]['commits'] += 1
    elif current_author:
        parts = line.split('\t')
        if len(parts) == 3:
            added, deleted, filename = parts
            if added != '-': authors[current_author]['added'] += int(added)
            if deleted != '-': authors[current_author]['deleted'] += int(deleted)

for author, stats in sorted(authors.items(), key=lambda x: (x[1]['added'] + x[1]['deleted']), reverse=True):
    print(f"{author}|{stats['commits']}|{stats['added']}|{stats['deleted']}")
