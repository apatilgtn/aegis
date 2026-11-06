FROM node:22.13-alpine AS dependencies
WORKDIR /app
COPY portal/package.json portal/package-lock.json ./
RUN npm ci

FROM node:22.13-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY portal/ ./
RUN npx next build --webpack

FROM node:22.13-alpine AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
RUN addgroup -S aegis && adduser -S -G aegis -u 10001 aegis
COPY --from=builder --chown=aegis:aegis /app/.next/standalone ./
COPY --from=builder --chown=aegis:aegis /app/.next/static ./.next/static
COPY --from=builder --chown=aegis:aegis /app/public ./public
USER aegis
EXPOSE 3000
CMD ["node", "server.js"]
