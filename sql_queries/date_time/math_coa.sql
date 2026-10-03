SELECT
    (COALESCE(discount_pct, 0)+ 10) new_discount_pct,
    discount_pct
FROM order_items;


SELECT 
    first_name,
    last_name,
    first_name + ' ' + (COALESCE(maiden_name, ' ')) + last_name as full_name
FROM customers;