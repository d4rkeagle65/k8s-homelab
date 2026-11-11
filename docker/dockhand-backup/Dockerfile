# SQLite for a consistent copy of a live database, 7-Zip for the encrypted
# archive, rclone for Garage. backup.sh is the whole job (compose.yaml says what
# it does).
FROM alpine:3.24

RUN apk add --no-cache sqlite 7zip rclone \
 && { command -v 7z >/dev/null || ln -s "$(command -v 7zz)" /usr/local/bin/7z; } \
 && sqlite3 --version >/dev/null && 7z i >/dev/null && rclone version >/dev/null

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod 0755 /usr/local/bin/backup.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]
CMD ["serve"]
