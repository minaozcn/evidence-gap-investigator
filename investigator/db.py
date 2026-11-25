"""MySQL connection for the investigator."""

import os

import pymysql
from pymysql.cursors import DictCursor


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        cursorclass=DictCursor,               # rows come back as dicts
        init_command="SET time_zone = '+00:00'",  # all times are UTC
        # Without autocommit, a long-lived connection keeps reading the same
        # snapshot and would not see evidence added later.
        autocommit=True,
    )
