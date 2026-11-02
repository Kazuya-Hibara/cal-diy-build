#!/bin/sh
# Boot script for the slim (Next standalone) image. Mirrors upstream scripts/start.sh
# step by step; differences are only what standalone output requires.
set -x
cd /calcom

# 1. Replace the statically built BUILT_NEXT_PUBLIC_WEBAPP_URL with run-time NEXT_PUBLIC_WEBAPP_URL
#    (upstream script: apps/web/.next + apps/web/public). Standalone's server.js also embeds the
#    serialized next config (rewrites/redirects with the URL), so replace it there too.
scripts/replace-placeholder.sh "$BUILT_NEXT_PUBLIC_WEBAPP_URL" "$NEXT_PUBLIC_WEBAPP_URL"
if [ "$BUILT_NEXT_PUBLIC_WEBAPP_URL" != "$NEXT_PUBLIC_WEBAPP_URL" ]; then
  sed -i -e "s|$BUILT_NEXT_PUBLIC_WEBAPP_URL|$NEXT_PUBLIC_WEBAPP_URL|g" apps/web/server.js
fi

# 2. Same as upstream: wait for DB, migrate, seed app store.
scripts/wait-for-it.sh ${DATABASE_HOST} -- echo "database is up"
/opt/prisma-cli/node_modules/.bin/prisma migrate deploy --schema /calcom/packages/prisma/schema.prisma
node /calcom/scripts/seed-app-store.js

# 3. `next start` evaluates next.config.ts at boot, which sets these process.env defaults inside the
#    server process. The standalone server.js uses a build-time serialized config instead, so apply
#    the same defaults here (only when unset, exactly as next.config.ts does).
if [ -n "$NEXT_PUBLIC_SINGLE_ORG_SLUG" ]; then
  unset RESERVED_SUBDOMAINS
  [ -z "$ORGANIZATIONS_ENABLED" ] && export ORGANIZATIONS_ENABLED=1
fi
if [ "$NODE_ENV" = "production" ] || [ "$CALCOM_ENV" = "production" ]; then
  export TRIGGER_VERSION="$(cat /calcom/scripts/trigger-version)"
fi
if [ -z "$NEXTAUTH_URL" ] && [ -n "$NEXT_PUBLIC_WEBAPP_URL" ]; then
  export NEXTAUTH_URL="$NEXT_PUBLIC_WEBAPP_URL/api/auth"
fi
if [ -z "$NEXT_PUBLIC_WEBSITE_URL" ]; then
  export NEXT_PUBLIC_WEBSITE_URL="$NEXT_PUBLIC_WEBAPP_URL"
fi

# 4. Standalone server reads HOSTNAME; Docker sets it to the container id, which would bind only the
#    container IP and break the localhost healthcheck. `next start` listens on all interfaces.
export HOSTNAME=0.0.0.0
export PORT="${PORT:-3000}"
exec node /calcom/apps/web/server.js
