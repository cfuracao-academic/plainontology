--
-- PostgreSQL database dump (schema only), in the style of pg_dump --schema-only.
-- The library example from library.sql, plus a loan status enum and an array column.
--

SET statement_timeout = 0;
SET lock_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET client_min_messages = warning;

CREATE TYPE public.loan_status AS ENUM (
    'open',
    'returned',
    'lost'
);

SET default_tablespace = '';
SET default_table_access_method = heap;

CREATE TABLE public.authors (
    id integer NOT NULL,
    name text NOT NULL,
    born date,
    country text
);

CREATE SEQUENCE public.authors_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.authors_id_seq OWNED BY public.authors.id;

CREATE TABLE public.books (
    id integer NOT NULL,
    title text NOT NULL,
    isbn character varying(13),
    published date,
    in_print boolean,
    keywords text[],
    author_id integer NOT NULL
);

CREATE TABLE public.tags (
    id integer NOT NULL,
    label text NOT NULL
);

CREATE TABLE public.book_tags (
    book_id integer NOT NULL,
    tag_id integer NOT NULL
);

CREATE TABLE public.loans (
    id integer NOT NULL,
    book_id integer NOT NULL,
    member text NOT NULL,
    loaned_at timestamp with time zone NOT NULL,
    returned_at timestamp with time zone,
    status public.loan_status DEFAULT 'open'::public.loan_status NOT NULL
);

ALTER TABLE ONLY public.authors ALTER COLUMN id SET DEFAULT nextval('public.authors_id_seq'::regclass);

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_isbn_key UNIQUE (isbn);

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.book_tags
    ADD CONSTRAINT book_tags_pkey PRIMARY KEY (book_id, tag_id);

ALTER TABLE ONLY public.loans
    ADD CONSTRAINT loans_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX tags_label_idx ON public.tags USING btree (label);

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.authors(id);

ALTER TABLE ONLY public.book_tags
    ADD CONSTRAINT book_tags_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id);

ALTER TABLE ONLY public.book_tags
    ADD CONSTRAINT book_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(id);

ALTER TABLE ONLY public.loans
    ADD CONSTRAINT loans_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id);

--
-- PostgreSQL database dump complete
--
