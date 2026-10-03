SELECT 
    DATEADD(year, 2, DeliveredAt) DateDel,
    DATEDIFF(year, 3, DeliveredAt) DateDeleted
FROM dbo.Orders;
