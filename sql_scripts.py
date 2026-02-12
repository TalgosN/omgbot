union = '''

WITH anki as ( -------времянка по анкетам

	WITH base as (

	SELECT  dt_shift,
			club, 
			CAST(COUNT(DISTINCT shift_second_name|| ' '|| shift_first_name) AS FLOAT) as rn
	FROM shifts 

	GROUP BY dt_shift,
			 club 
)

		SELECT ns.login,
			   sh.dt_shift,
			   sh.club,
			   coalesce(COUNT(DISTINCT ank.ID) *1.0 /b.rn,0) as cnt_ank

		FROM shifts sh
        
        JOIN 
            users_new ns ON sh.shift_second_name = ns.second_name
            AND sh.shift_first_name = ns.first_name


		

		LEFT JOIN anketi ank 
			ON date(sh.dt_shift)=date(ank.dt_ank) 
			AND sh.club=ank.club_ank

		LEFT JOIN base b
			ON date(b.dt_shift)=date(ank.dt_ank) 
			AND b.club=ank.club_ank
			
		GROUP BY 	ns.login,  
					sh.dt_shift,
					sh.club, 
					sh.shift_second_name,
                    sh.shift_first_name


)
----ДР

SELECT 		date(br.dt_rep) 			as dt_rep,
			br.who 						as s_name,
			'ДР'						as kpi,
			count(distinct br.id) 		as fact
			
FROM birthday br

WHERE br.status = 'Одобрено'

GROUP BY	date(br.dt_rep),
			br.who,
			'ДР'
------------------------------------------------------------------------------------------------------------------------
UNION -- продления

SELECT  date(fp.dt_rep) 				as dt_rep,
		fp.who 							as s_name,
		'Продления' 					as kpi,
		count(distinct fp.id) 			as fact

FROM afterparty fp

GROUP BY 	date(fp.dt_rep),
			fp.who,
			'Продления'

-----------------------------------------------------------------------------------------------------------------------------
UNION   --анкеты

SELECT date(nk.dt_shift) as dt_rep,
		nk.login as s_name,
		'Анкеты' as kpi,
		nk.cnt_ank as fact
		
FROM anki nk

-----------------------------------------------------------------------------------------------------------------------------------------
UNION  --сертификаты

SELECT date(st.d_rep) 					as dt_rep,
	   st.who 							as s_name,
	   'Сертификаты' 					as kpi,
	   sum(st.bonus) 					as fact

FROM sert st

GROUP BY 	date(st.d_rep),
			st.who,
			'Сертификаты'
------------------------------------------------------------------------------------------------------------------------------------------
UNION --абики

SELECT date(bk.d_rep) 					as dt_rep,
	   bk.who 							as s_name,
	   'Абонементы' 					as kpi,
	   sum(bk.bonus) 					as fact

FROM abik bk

GROUP BY	date(bk.d_rep), 
			bk.who,
			'Абонементы'
--------------------------------------------------------------------------------------------------------------------------------------
UNION --инициативы

SELECT 		date(nt.dt_rep) 			as dt_rep,
			nt.who 						as s_name,
			'Инициативы'				as kpi,
			count(distinct nt.id) 		as fact
			
FROM initiative nt



GROUP BY	date(nt.dt_rep),
			nt.who,
			'Инициативы'
----------------------------------------------------------------------------------------------------------------------------------------
UNION --БС

SELECT 		date(bs.dt_bs) 				as dt_rep,
			bs.name_bs					as s_name,
			'БС'						as kpi,
			count(distinct bs.id_bs)	as fact
FROM bs

GROUP BY	date(bs.dt_bs),
			bs.name_bs,
			'БС'
------------------------------------------------------------------------------------------------------------------------------------------
UNION --штрафы

SELECT		date(pn.dt)					as dt_rep,
			pn.name						as s_name,
			'Штрафы'					as kpi,
			count(distinct pn.ID)		as fact
FROM penalty pn

GROUP BY	date(pn.dt),
			pn.name,
			'Штрафы'

UNION --отзывы

SELECT 		date(rv.d_rep) 				as dt_rep,
			rv.who 						as s_name,
			'Отзывы'					as kpi,
			sum (rv.amount) 			as fact
			
FROM reviews rv


GROUP BY	date(rv.d_rep) ,
			rv.who ,
			'Отзывы'          

'''


shifts='''
SELECT 
    DATE(sh.dt_shift, 'start of month', '+1 month', '-1 day') AS last_day_of_month,
    ns.login AS s_name,
  sum(sh.dur) as cnt_h, --часов
    SUM(ROUND(dur / 6, 3)) AS total_cnt_smen --к-во смен
FROM 
    shifts sh
JOIN 
    users_new ns ON sh.shift_second_name = ns.second_name
                  AND sh.shift_first_name = ns.first_name
GROUP BY 
    last_day_of_month, ns.login;

    '''

shifts_ext='''
SELECT 
    *
FROM 
    shifts;
    '''

records='''
WITH all_records AS (
-------времянка по анкетам
	WITH anki as ( 
	
		WITH base as (
	
		SELECT  dt_shift,
				club, 
				CAST(COUNT(DISTINCT shift_second_name|| ' '|| shift_first_name) AS FLOAT) as rn
		FROM shifts 
	
		GROUP BY dt_shift,
				 club 
	)
	
			SELECT ns.login,
				   sh.dt_shift,
				   sh.club,
				   coalesce(COUNT(DISTINCT ank.ID) *1.0 /b.rn,0) as cnt_ank
	
			FROM shifts sh
	        
	        JOIN 
	            users_new ns ON sh.shift_second_name = ns.second_name
	            AND sh.shift_first_name = ns.first_name
	
	
			
	
			LEFT JOIN anketi ank 
				ON date(sh.dt_shift)=date(ank.dt_ank) 
				AND sh.club=ank.club_ank
	
			LEFT JOIN base b
				ON date(b.dt_shift)=date(ank.dt_ank) 
				AND b.club=ank.club_ank
				
			GROUP BY 	ns.login,  
						sh.dt_shift,
						sh.club, 
						sh.shift_second_name,
	                    sh.shift_first_name
	)
	----ДР
	
	SELECT 		date(br.dt_rep) 			as dt_rep,
				br.who 						as s_name,
				'ДР'						as kpi,
				count(distinct br.id) 		as fact
				
	FROM birthday br
	
	WHERE br.status = 'Одобрено'
	
	GROUP BY	date(br.dt_rep),
				br.who,
				'ДР'
	------------------------------------------------------------------------------------------------------------------------
	UNION -- продления
	
	SELECT  date(fp.dt_rep) 				as dt_rep,
			fp.who 							as s_name,
			'Продления' 					as kpi,
			count(distinct fp.id) 			as fact
	
	FROM afterparty fp
	
	GROUP BY 	date(fp.dt_rep),
				fp.who,
				'Продления'
	
	-----------------------------------------------------------------------------------------------------------------------------
	UNION   --анкеты
	
	SELECT date(nk.dt_shift) as dt_rep,
			nk.login as s_name,
			'Анкеты' as kpi,
			nk.cnt_ank as fact
			
	FROM anki nk
	
	-----------------------------------------------------------------------------------------------------------------------------------------
	UNION  --сертификаты
	
	SELECT date(st.d_rep) 					as dt_rep,
		   st.who 							as s_name,
		   'Сертификаты' 					as kpi,
		   sum(st.bonus) 					as fact
	
	FROM sert st
	
	GROUP BY 	date(st.d_rep),
				st.who,
				'Сертификаты'
	------------------------------------------------------------------------------------------------------------------------------------------
	UNION --абики
	
	SELECT date(bk.d_rep) 					as dt_rep,
		   bk.who 							as s_name,
		   'Абонементы' 					as kpi,
		   sum(bk.bonus) 					as fact
	
	FROM abik bk
	
	GROUP BY	date(bk.d_rep), 
				bk.who,
				'Абонементы'
	--------------------------------------------------------------------------------------------------------------------------------------
	UNION --инициативы
	
	SELECT 		date(nt.dt_rep) 			as dt_rep,
				nt.who 						as s_name,
				'Инициативы'				as kpi,
				count(distinct nt.id) 		as fact
				
	FROM initiative nt
	
	
	
	GROUP BY	date(nt.dt_rep),
				nt.who,
				'Инициативы'
	----------------------------------------------------------------------------------------------------------------------------------------
	UNION --БС
	
	SELECT 		date(bs.dt_bs) 				as dt_rep,
				bs.name_bs					as s_name,
				'БС'						as kpi,
				count(distinct bs.id_bs)	as fact
	FROM bs
	
	GROUP BY	date(bs.dt_bs),
				bs.name_bs,
				'БС'
	------------------------------------------------------------------------------------------------------------------------------------------
	UNION --штрафы
	
	SELECT		date(pn.dt)					as dt_rep,
				pn.name						as s_name,
				'Штрафы'					as kpi,
				count(distinct pn.ID)		as fact
	FROM penalty pn
	
	GROUP BY	date(pn.dt),
				pn.name,
				'Штрафы'
	
	UNION --отзывы
	
	SELECT 		date(rv.d_rep) 				as dt_rep,
				rv.who 						as s_name,
				'Отзывы'					as kpi,
				sum (rv.amount) 			as fact
				
	FROM reviews rv
	
	GROUP BY	date(rv.d_rep) ,
				rv.who ,
				'Отзывы'
)


SELECT s_name, kpi, sum(fact) as total
FROM all_records 
GROUP BY 1,2
ORDER BY 2,3 DESC, 1;

    '''