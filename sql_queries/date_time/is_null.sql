SELECT 
    AVG(COALESCE(discount_pct, 0)) actual_discout_avg
FROM order_items;

SELECT * FROM order_items;