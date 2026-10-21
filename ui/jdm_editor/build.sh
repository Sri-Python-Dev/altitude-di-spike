#!/bin/sh
# Rebuild ui/static/jdm-editor.{js,css}: the GoRules JDM editor (@gorules/jdm-editor 1.52.0) bundled
# for the Rules page. Built once and committed, so the demo needs no CDN. Needs Node; runs in a temp dir.
set -e
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
cp "$here/entry.jsx" "$work/"
cd "$work"
echo '{"name":"altitude-jdm-editor-bundle","private":true}' > package.json
npm install --ignore-scripts --no-audit --no-fund --loglevel=error @gorules/jdm-editor@1.52.0 react@18.3.1 react-dom@18.3.1 esbuild@0.25.0
node_modules/@esbuild/*/bin/esbuild entry.jsx --bundle --minify --format=iife --target=es2020 \
  --define:process.env.NODE_ENV='"production"' --loader:.ttf=dataurl --loader:.woff=dataurl --loader:.woff2=dataurl \
  --loader:.svg=dataurl --loader:.png=dataurl --outfile="$here/../static/jdm-editor.js"
echo "Built $here/../static/jdm-editor.js"
