import json, os

result = []
for r, d, files in os.walk('tests'):
    for f in files:
        if f.endswith('.py'):
            filepath = os.path.join(r, f)
            try:
                import ast
                with open(filepath, 'r', encoding='utf-8') as f_obj:
                    source = f_obj.read()
                tree = ast.parse(source)
                classes = []
                functions = []
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                        classes.append({'name': node.name, 'methods': methods, 'doc': ast.get_docstring(node)})
                    elif isinstance(node, ast.Import):
                        for alias in node.names: imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module if node.module else ''
                        for alias in node.names: imports.append(f'{module}.{alias.name}')
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef):
                        functions.append({'name': node.name, 'doc': ast.get_docstring(node)})
                result.append({'path': filepath, 'module_doc': ast.get_docstring(tree), 'classes': classes, 'functions': functions, 'imports': imports})
            except: pass

with open(r'C:\Users\anshu\.gemini\antigravity-cli\brain\e8e09973-7aa3-4b0e-a9ec-2873c9bd4953\scratch\trace_lite_report.md', 'a', encoding='utf-8') as f_md:
    for file_info in result:
        f_md.write('\n### File: ' + str(file_info.get('path')) + '\n')
        doc = file_info.get('module_doc', '')
        if doc: f_md.write('**Purpose**: ' + doc.splitlines()[0] + '\n')
        classes = file_info.get('classes', [])
        if classes:
            f_md.write('**Classes**:\n')
            for c in classes:
                c_doc = c.get('doc') or ''
                c_doc_trunc = c_doc.splitlines()[0] if c_doc else ''
                methods = ', '.join(c['methods']) if c['methods'] else 'None'
                f_md.write('- ' + c['name'] + ': ' + c_doc_trunc + '\n')
                f_md.write('  - Methods: ' + methods + '\n')
        functions = file_info.get('functions', [])
        if functions:
            f_md.write('**Functions**:\n')
            for fn in functions:
                f_doc = fn.get('doc') or ''
                f_doc_trunc = f_doc.splitlines()[0] if f_doc else ''
                f_md.write('- ' + fn['name'] + ': ' + f_doc_trunc + '\n')
        imports = file_info.get('imports', [])
        if imports:
            f_md.write('**Imports**: ' + ', '.join(imports[:20]) + ('...' if len(imports) > 20 else '') + '\n')
