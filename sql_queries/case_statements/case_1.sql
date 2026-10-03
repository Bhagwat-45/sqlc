SELECT
    cu_ratings,
    count(rating) as total_ratings
FROM(
    SELECT
    title,
    rating,
    CASE 
        WHEN rating > 4.0 THEN 'Great Product'
        WHEN rating > 3.0 THEN 'Good Prodcut'
        ELSE 'Average'
    END cu_ratings
    FROM products
)t
GROUP BY cu_ratings;