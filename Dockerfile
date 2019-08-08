FROM alpine:3.9

RUN apk --no-cache update && \
    apk add --no-cache python3 util-linux

COPY tasksetd.py /

CMD ["/tasksetd.py"]
