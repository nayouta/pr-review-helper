const fs = require('fs');
const ts = require('typescript');

const filePath = process.argv[2];
const code = fs.readFileSync(filePath, 'utf8');
const sourceFile = ts.createSourceFile(filePath, code, ts.ScriptTarget.Latest, true);

let results = [];

function visit(node) {
  if (ts.isCallExpression(node)) {
    const text = node.expression.getText();
    if (["console.log", "console.debug"].some(d => text.includes(d))) {
      results.push({
        line: sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1,
        content: text,
        reason: "💻 JS/TS debug output"
      });
    }
  }
  if (ts.isDebuggerStatement(node)) {
    results.push({
      line: sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1,
      content: "debugger",
      reason: "💻 JS/TS debug output"
    });
  }
  if (ts.isNumericLiteral(node)) {
    results.push({
      line: sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1,
      content: node.getText(),
      reason: "🔢 Use of magic number"
    });
  }

  ts.forEachChild(node, visit);
}

visit(sourceFile);
console.log(JSON.stringify(results, null, 2));
