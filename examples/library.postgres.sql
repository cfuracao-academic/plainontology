--
-- PostgreSQL database dump (schema only), in the style of pg_dump --schema-only.
-- The library example from library.sql, plus PostgreSQL features: an enum, a domain, an array,
-- comments, CHECK constraints, a view, a trigger, a second schema, a join table with data
-- and a partitioned table.
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

CREATE SCHEMA archive;

CREATE DOMAIN public.card_number AS character varying(10)
	CONSTRAINT card_number_check CHECK (((VALUE)::text ~ '^C-[0-9]+$'::text));

CREATE FUNCTION public.touch_loan() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

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
    pages integer,
    author_id integer NOT NULL,
    CONSTRAINT books_pages_check CHECK ((pages > 0))
);

COMMENT ON TABLE public.books IS 'A title the library holds; one record per edition.';

COMMENT ON COLUMN public.books.isbn IS 'ISBN-13 without hyphens.';

CREATE TABLE public.awards (
    id integer NOT NULL,
    name text NOT NULL
);

CREATE TABLE public.book_awards (
    book_id integer NOT NULL,
    award_id integer NOT NULL,
    year integer NOT NULL,
    CONSTRAINT book_awards_year_check CHECK (((year >= 1900) AND (year <= 2100)))
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
    member public.card_number NOT NULL,
    loaned_at timestamp with time zone NOT NULL,
    returned_at timestamp with time zone,
    status public.loan_status DEFAULT 'open'::public.loan_status NOT NULL,
    updated_at timestamp with time zone,
    CONSTRAINT loans_dates_check CHECK (((returned_at IS NULL) OR (returned_at >= loaned_at)))
);

COMMENT ON COLUMN public.loans.member IS 'Library card number.';

CREATE TABLE archive.loans (
    id integer NOT NULL,
    book_id integer NOT NULL,
    closed_at timestamp with time zone NOT NULL
);

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    logged_at timestamp with time zone NOT NULL,
    action text NOT NULL,
    loan_id integer
)
PARTITION BY RANGE (logged_at);

CREATE TABLE public.audit_log_2026 (
    id integer NOT NULL,
    logged_at timestamp with time zone NOT NULL,
    action text NOT NULL,
    loan_id integer
);

CREATE TABLE public.audit_log_2027 (
    id integer NOT NULL,
    logged_at timestamp with time zone NOT NULL,
    action text NOT NULL,
    loan_id integer
);

ALTER TABLE ONLY public.audit_log ATTACH PARTITION public.audit_log_2026 FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');

ALTER TABLE ONLY public.audit_log ATTACH PARTITION public.audit_log_2027 FOR VALUES FROM ('2027-01-01 00:00:00+00') TO ('2028-01-01 00:00:00+00');

CREATE VIEW public.open_loans AS
 SELECT l.id,
    l.member,
    b.title AS book_title,
    l.loaned_at
   FROM (public.loans l
     JOIN public.books b ON ((b.id = l.book_id)))
  WHERE (l.returned_at IS NULL);

COMMENT ON VIEW public.open_loans IS 'Loans not yet returned.';

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

ALTER TABLE ONLY public.awards
    ADD CONSTRAINT awards_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.book_awards
    ADD CONSTRAINT book_awards_pkey PRIMARY KEY (book_id, award_id);

ALTER TABLE ONLY archive.loans
    ADD CONSTRAINT loans_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id, logged_at);

CREATE UNIQUE INDEX tags_label_idx ON public.tags USING btree (label);

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.authors(id);

ALTER TABLE ONLY public.book_tags
    ADD CONSTRAINT book_tags_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id);

ALTER TABLE ONLY public.book_tags
    ADD CONSTRAINT book_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(id);

ALTER TABLE ONLY public.loans
    ADD CONSTRAINT loans_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id);

ALTER TABLE ONLY public.book_awards
    ADD CONSTRAINT book_awards_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id);

ALTER TABLE ONLY public.book_awards
    ADD CONSTRAINT book_awards_award_id_fkey FOREIGN KEY (award_id) REFERENCES public.awards(id);

ALTER TABLE ONLY archive.loans
    ADD CONSTRAINT loans_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id);

ALTER TABLE ONLY public.audit_log_2026
    ADD CONSTRAINT audit_log_2026_loan_id_fkey FOREIGN KEY (loan_id) REFERENCES public.loans(id);

CREATE TRIGGER loans_touch BEFORE INSERT OR UPDATE ON public.loans FOR EACH ROW EXECUTE FUNCTION public.touch_loan();

--
-- PostgreSQL database dump complete
--
