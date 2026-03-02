-- ============================================================
-- Data migration: Move webside.gr data from public → webside_gr schema
-- Run AFTER creating the first tenant via:
--   python manage.py create_tenant \
--     --name "WebSide.gr" --slug webside-gr --schema webside_gr \
--     --domain webside.gr --owner-email vassilistotskas@msn.com \
--     --plan pro --store-name "WebSide.gr" \
--     --extra-domains api.webside.gr
--
-- The create_tenant command auto-creates the schema and runs migrations.
-- This script copies existing data from public to the new tenant schema.
-- ============================================================

BEGIN;

-- ─── User & Auth tables ────────────────────────────────────
INSERT INTO webside_gr.user_useraccount
SELECT * FROM public.user_useraccount;

INSERT INTO webside_gr.user_useraddress
SELECT * FROM public.user_useraddress;

INSERT INTO webside_gr.user_usersubscription
SELECT * FROM public.user_usersubscription;

INSERT INTO webside_gr.user_subscriptiontopic
SELECT * FROM public.user_subscriptiontopic;

INSERT INTO webside_gr.auth_group
SELECT * FROM public.auth_group;

INSERT INTO webside_gr.auth_group_permissions
SELECT * FROM public.auth_group_permissions;

INSERT INTO webside_gr.auth_permission
SELECT * FROM public.auth_permission;

INSERT INTO webside_gr.user_useraccount_groups
SELECT * FROM public.user_useraccount_groups;

INSERT INTO webside_gr.user_useraccount_user_permissions
SELECT * FROM public.user_useraccount_user_permissions;

-- Knox tokens
INSERT INTO webside_gr.knox_authtoken
SELECT * FROM public.knox_authtoken;

-- Allauth tables
INSERT INTO webside_gr.account_emailaddress
SELECT * FROM public.account_emailaddress;

INSERT INTO webside_gr.account_emailconfirmation
SELECT * FROM public.account_emailconfirmation;

INSERT INTO webside_gr.socialaccount_socialaccount
SELECT * FROM public.socialaccount_socialaccount;

INSERT INTO webside_gr.socialaccount_socialtoken
SELECT * FROM public.socialaccount_socialtoken;

INSERT INTO webside_gr.socialaccount_socialapp
SELECT * FROM public.socialaccount_socialapp;

INSERT INTO webside_gr.socialaccount_socialapp_sites
SELECT * FROM public.socialaccount_socialapp_sites;

INSERT INTO webside_gr.usersessions_usersession
SELECT * FROM public.usersessions_usersession;

-- MFA
INSERT INTO webside_gr.mfa_authenticator
SELECT * FROM public.mfa_authenticator;

-- ─── Product tables ────────────────────────────────────────
INSERT INTO webside_gr.product_productcategory
SELECT * FROM public.product_productcategory;

INSERT INTO webside_gr.product_productcategory_translation
SELECT * FROM public.product_productcategory_translation;

INSERT INTO webside_gr.product_productcategoryimage
SELECT * FROM public.product_productcategoryimage;

INSERT INTO webside_gr.product_product
SELECT * FROM public.product_product;

INSERT INTO webside_gr.product_product_translation
SELECT * FROM public.product_product_translation;

INSERT INTO webside_gr.product_productimage
SELECT * FROM public.product_productimage;

INSERT INTO webside_gr.product_productreview
SELECT * FROM public.product_productreview;

INSERT INTO webside_gr.product_productfavourite
SELECT * FROM public.product_productfavourite;

INSERT INTO webside_gr.product_attribute
SELECT * FROM public.product_attribute;

INSERT INTO webside_gr.product_attribute_translation
SELECT * FROM public.product_attribute_translation;

INSERT INTO webside_gr.product_attributevalue
SELECT * FROM public.product_attributevalue;

INSERT INTO webside_gr.product_attributevalue_translation
SELECT * FROM public.product_attributevalue_translation;

INSERT INTO webside_gr.product_productattribute
SELECT * FROM public.product_productattribute;

-- ─── Order tables ──────────────────────────────────────────
INSERT INTO webside_gr.order_order
SELECT * FROM public.order_order;

INSERT INTO webside_gr.order_orderitem
SELECT * FROM public.order_orderitem;

INSERT INTO webside_gr.order_stockreservation
SELECT * FROM public.order_stockreservation;

INSERT INTO webside_gr.order_stocklog
SELECT * FROM public.order_stocklog;

-- ─── Cart tables ───────────────────────────────────────────
INSERT INTO webside_gr.cart_cart
SELECT * FROM public.cart_cart;

INSERT INTO webside_gr.cart_cartitem
SELECT * FROM public.cart_cartitem;

-- ─── Blog tables ───────────────────────────────────────────
INSERT INTO webside_gr.blog_blogcategory
SELECT * FROM public.blog_blogcategory;

INSERT INTO webside_gr.blog_blogcategory_translation
SELECT * FROM public.blog_blogcategory_translation;

INSERT INTO webside_gr.blog_blogauthor
SELECT * FROM public.blog_blogauthor;

INSERT INTO webside_gr.blog_blogpost
SELECT * FROM public.blog_blogpost;

INSERT INTO webside_gr.blog_blogpost_translation
SELECT * FROM public.blog_blogpost_translation;

INSERT INTO webside_gr.blog_blogcomment
SELECT * FROM public.blog_blogcomment;

INSERT INTO webside_gr.blog_blogtag
SELECT * FROM public.blog_blogtag;

-- ─── Supporting tables ─────────────────────────────────────
INSERT INTO webside_gr.vat_vat
SELECT * FROM public.vat_vat;

INSERT INTO webside_gr.pay_way_payway
SELECT * FROM public.pay_way_payway;

INSERT INTO webside_gr.pay_way_payway_translation
SELECT * FROM public.pay_way_payway_translation;

INSERT INTO webside_gr.tag_tag
SELECT * FROM public.tag_tag;

INSERT INTO webside_gr.tag_taggeditem
SELECT * FROM public.tag_taggeditem;

INSERT INTO webside_gr.contact_contact
SELECT * FROM public.contact_contact;

INSERT INTO webside_gr.notification_notification
SELECT * FROM public.notification_notification;

INSERT INTO webside_gr.notification_notificationuser
SELECT * FROM public.notification_notificationuser;

INSERT INTO webside_gr.search_searchquery
SELECT * FROM public.search_searchquery;

INSERT INTO webside_gr.search_searchclick
SELECT * FROM public.search_searchclick;

-- Loyalty
INSERT INTO webside_gr.loyalty_loyaltytier
SELECT * FROM public.loyalty_loyaltytier;

INSERT INTO webside_gr.loyalty_loyaltytier_translation
SELECT * FROM public.loyalty_loyaltytier_translation;

INSERT INTO webside_gr.loyalty_pointstransaction
SELECT * FROM public.loyalty_pointstransaction;

-- Extra settings
INSERT INTO webside_gr.extra_settings_setting
SELECT * FROM public.extra_settings_setting;

-- Celery Beat / Results (per-tenant)
INSERT INTO webside_gr.django_celery_beat_periodictask
SELECT * FROM public.django_celery_beat_periodictask;

INSERT INTO webside_gr.django_celery_beat_crontabschedule
SELECT * FROM public.django_celery_beat_crontabschedule;

INSERT INTO webside_gr.django_celery_beat_intervalschedule
SELECT * FROM public.django_celery_beat_intervalschedule;

INSERT INTO webside_gr.django_celery_beat_solarschedule
SELECT * FROM public.django_celery_beat_solarschedule;

INSERT INTO webside_gr.django_celery_beat_clockedschedule
SELECT * FROM public.django_celery_beat_clockedschedule;

INSERT INTO webside_gr.django_celery_results_taskresult
SELECT * FROM public.django_celery_results_taskresult;

-- Simple History (if applicable — may be large)
-- INSERT INTO webside_gr.product_historicalproduct
-- SELECT * FROM public.product_historicalproduct;
-- (Add more historical tables as needed)

-- ─── Reset sequences ───────────────────────────────────────
-- Reset all sequences in the tenant schema to avoid PK collisions
DO $$
DECLARE
    r RECORD;
    max_val BIGINT;
BEGIN
    FOR r IN
        SELECT schemaname, sequencename
        FROM pg_sequences
        WHERE schemaname = 'webside_gr'
    LOOP
        EXECUTE format(
            'SELECT setval(%L, COALESCE((SELECT MAX(id) FROM %I.%I), 0) + 1, false)',
            'webside_gr.' || r.sequencename,
            r.schemaname,
            replace(r.sequencename, '_id_seq', '')
        );
    EXCEPTION WHEN OTHERS THEN
        -- Skip sequences that don't correspond to tables with id column
        NULL;
    END LOOP;
END $$;

COMMIT;
