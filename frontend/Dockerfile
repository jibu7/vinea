FROM node:22-alpine
WORKDIR /app
# `npm ci`, and the lockfile named without a glob. Both halves are the same point.
#
# `npm install` is allowed to *change* resolution: when it decides `package-lock.json` and
# `package.json` disagree it rewrites the lockfile inside the layer and installs whatever it
# just resolved. `package-lock.json*` made a missing lockfile not an error either. Between
# them the image could be built from a dependency tree that is neither the one committed nor
# the one CI tested — silently, with a green build, and the only way to find out would be a
# bug that reproduces in the container and nowhere else.
#
# `npm ci` installs exactly the lockfile and fails loudly when it cannot: no lockfile, or one
# out of step with `package.json`, stops the build here rather than resolving around it.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]
