-- A small lending library. Used by the README, the tests and the CI demo.
CREATE TABLE authors (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    born    DATE,
    country TEXT
);

CREATE TABLE books (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    isbn      VARCHAR(13) UNIQUE,
    published DATE,
    in_print  BOOLEAN,
    author_id INTEGER NOT NULL REFERENCES authors(id)
);

CREATE TABLE tags (
    id    INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

CREATE TABLE book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id),
    tag_id  INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (book_id, tag_id)
);

CREATE TABLE loans (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    member      TEXT NOT NULL,
    loaned_at   TIMESTAMP NOT NULL,
    returned_at TIMESTAMP
);
