SELECT 
    gender_b,
    count(gender_b) as no_of_people_in_each_gender
FROM (
    SELECT 
    first_name,
    CASE
        WHEN gender IS NULL THEN  'N/A'
        WHEN gender = 'male' THEN 'M'
        ELSE 'F'
    END gender_b
    FROM customers
)t 
GROUP BY gender_b;
