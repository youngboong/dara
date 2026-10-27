# DARA web front end development

For normal use, run `START.cmd` in the repository root. Node.js is only needed
when you modify the front end. The Python server serves the bundled `public/`
build, so do not delete this folder as if it were a disposable artifact.

Install the dependencies and start the development server in this directory:

```console
npm ci
npm run develop
```

In development mode the API address is `http://localhost:8001/api`.
Run the backend separately from the Python environment with `dara server --port 8001`.
The default START server uses port 8898.

To build the screens that are shipped with the server, run the following command and
commit the changes under `public/` together with your source changes:

```console
npm run build
```

Pages are in `src/pages/`, shared UI in `src/components/`, and the in-app guide in `src/md-pages/`.
Do not commit the development cache `.cache/` or `node_modules/`.
