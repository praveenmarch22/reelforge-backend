# The render service (milestone B5): Remotion with its own headless Chrome and FFmpeg.
# Built once; the Remotion bundle is made when the container starts (see server.mjs).
# Pinned by digest (QB5-013); update deliberately.
FROM node:22-bookworm-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb

# Chrome's libraries, plus fonts: Liberation stands in for Arial (same widths), Noto for
# emoji and other scripts, so captions look the same as in the editor's preview.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libdbus-1-3 libatk1.0-0 libgbm1 libasound2 libxrandr2 libxkbcommon0 libxfixes3 \
      libxcomposite1 libxdamage1 libatk-bridge2.0-0 libpango-1.0-0 libcairo2 libcups2 \
      fonts-liberation fonts-noto-core fonts-noto-color-emoji ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npx remotion browser ensure
COPY tsconfig.json server.mjs ./
COPY src ./src

# Runs as the unprivileged "node" user that the base image provides.
RUN chown -R node:node /app
USER node
ENV PORT=3000 NODE_ENV=production
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=240s --retries=5 \
  CMD node -e "fetch('http://localhost:3000/health').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"
CMD ["node", "server.mjs"]
