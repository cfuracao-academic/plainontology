# PlainOntology for VS Code

Syntax highlighting for PlainOntology `.onto` files: concepts, properties and relations, types and flags, rules, strings with language tags, comments, and the `[auto]` and `[proposed]` tags (shown dimmed, like comments, so your own lines stand out).

## Install from this folder

```bash
npm install -g @vscode/vsce
cd editors/vscode
vsce package                       # writes plainontology-0.2.0.vsix
code --install-extension plainontology-0.2.0.vsix
```

Or, while developing, copy this folder into your VS Code extensions folder (`~/.vscode/extensions/plainontology`) and reload the window.

## Other editors

`syntaxes/onto.tmLanguage.json` is a TextMate grammar, so it also works in Sublime Text, JetBrains IDEs (TextMate Bundles plugin) and any editor that reads TextMate grammars.
