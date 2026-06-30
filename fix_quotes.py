import re
with open('gen_filter_testcases.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace Chinese left/right double quotes with regular single quotes
content = content.replace('\u201c', "'").replace('\u201d', "'")

with open('gen_filter_testcases.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed")
