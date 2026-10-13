# Base images: run scripts/pin-base-images.sh to pin FROM lines by digest.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
LABEL org.opencontainers.image.source="https://github.com/LAKHAL-Farah/Cortex"
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=builder --chown=1000:1000 /app/public ./public
COPY --from=builder --chown=1000:1000 /app/.next/standalone ./
COPY --from=builder --chown=1000:1000 /app/.next/static ./.next/static
# Built-in `node` user (uid 1000); numeric so `runAsNonRoot: true` can verify it.
USER 1000:1000
EXPOSE 3000
CMD ["node", "server.js"]
