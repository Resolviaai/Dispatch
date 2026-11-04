with open('site/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

tag = '<meta name="google-site-verification" content="Ic_iRj7eikgo73cLDJzBrGZkJP5UxDBcWdFJIUIWUM0" />'
if 'google-site-verification' not in text:
    text = text.replace('<title>', tag + '\n    <title>')
    with open('site/index.html', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Added google-site-verification tag to site/index.html!")
