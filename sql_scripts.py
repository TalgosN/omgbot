union = '''

WITH anki as ( -------времянка по анкетам

	WITH base as (

	SELECT  date(substr(sh.dt_shift, 1, 10)) as dt_shift,
			sh.club,
			CAST(COUNT(DISTINCT COALESCE(
				NULLIF(lower(ns.login), ''),
				NULLIF(lower(sh.shift_login), ''),
				lower(sh.shift_second_name|| ' '|| sh.shift_first_name)
			)) AS FLOAT) as rn
	FROM shifts sh
	LEFT JOIN users ns ON (
		sh.shift_login IS NOT NULL
		AND lower(sh.shift_login) = lower(ns.login)
	) OR (
		sh.shift_login IS NULL
		AND sh.shift_second_name = ns.second_name
		AND sh.shift_first_name = ns.first_name
	)

	GROUP BY date(substr(sh.dt_shift, 1, 10)),
			 sh.club
)

		SELECT ns.login,
			   date(substr(sh.dt_shift, 1, 10)) as dt_shift,
			   sh.club,
			   coalesce(COUNT(DISTINCT ank.ID) *1.0 /b.rn,0) as cnt_ank

		FROM shifts sh
        
        JOIN users ns ON (
            sh.shift_login IS NOT NULL
            AND lower(sh.shift_login) = lower(ns.login)
        ) OR (
            sh.shift_login IS NULL
            AND sh.shift_second_name = ns.second_name
            AND sh.shift_first_name = ns.first_name
        )


		

		LEFT JOIN anketi ank 
			ON date(substr(sh.dt_shift, 1, 10))=date(substr(ank.dt_ank, 1, 10))
			AND sh.club=ank.club_ank

		LEFT JOIN base b
			ON b.dt_shift=date(substr(sh.dt_shift, 1, 10))
			AND b.club=sh.club
			
		GROUP BY 	ns.login,  
					date(substr(sh.dt_shift, 1, 10)),
					sh.club


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
    DATE(substr(sh.dt_shift, 1, 10), 'start of month', '+1 month', '-1 day') AS last_day_of_month,
    ns.login AS s_name,
  sum(sh.dur) as cnt_h, --часов
    SUM(ROUND(dur / 6, 3)) AS total_cnt_smen --к-во смен
FROM 
    shifts sh
JOIN users ns ON (
    sh.shift_login IS NOT NULL
    AND lower(sh.shift_login) = lower(ns.login)
) OR (
    sh.shift_login IS NULL
    AND sh.shift_second_name = ns.second_name
    AND sh.shift_first_name = ns.first_name
)
GROUP BY 
    last_day_of_month, ns.login;

    '''

shifts_ext='''
SELECT 
    COALESCE(ns.second_name, sh.shift_second_name) AS shift_second_name,
    COALESCE(ns.first_name, sh.shift_first_name) AS shift_first_name,
    sh.dt_shift,
    sh.club,
    sh.dur
FROM 
    shifts sh
LEFT JOIN users ns ON (
    sh.shift_login IS NOT NULL
    AND lower(sh.shift_login) = lower(ns.login)
) OR (
    sh.shift_login IS NULL
    AND sh.shift_second_name = ns.second_name
    AND sh.shift_first_name = ns.first_name
);
    '''

records='''
WITH all_records AS (
-------времянка по анкетам
	WITH anki as ( 
	
		WITH base as (
	
		SELECT  date(substr(sh.dt_shift, 1, 10)) as dt_shift,
				sh.club,
				CAST(COUNT(DISTINCT COALESCE(
					NULLIF(lower(ns.login), ''),
					NULLIF(lower(sh.shift_login), ''),
					lower(sh.shift_second_name|| ' '|| sh.shift_first_name)
				)) AS FLOAT) as rn
		FROM shifts sh
		LEFT JOIN users ns ON (
			sh.shift_login IS NOT NULL
			AND lower(sh.shift_login) = lower(ns.login)
		) OR (
			sh.shift_login IS NULL
			AND sh.shift_second_name = ns.second_name
			AND sh.shift_first_name = ns.first_name
		)

		GROUP BY date(substr(sh.dt_shift, 1, 10)),
				 sh.club
	)
	
			SELECT ns.login,
				   date(substr(sh.dt_shift, 1, 10)) as dt_shift,
				   sh.club,
				   coalesce(COUNT(DISTINCT ank.ID) *1.0 /b.rn,0) as cnt_ank
	
			FROM shifts sh
	        
	        JOIN users ns ON (
	            sh.shift_login IS NOT NULL
	            AND lower(sh.shift_login) = lower(ns.login)
	        ) OR (
	            sh.shift_login IS NULL
	            AND sh.shift_second_name = ns.second_name
	            AND sh.shift_first_name = ns.first_name
	        )
	
	
			
	
			LEFT JOIN anketi ank 
				ON date(substr(sh.dt_shift, 1, 10))=date(substr(ank.dt_ank, 1, 10))
				AND sh.club=ank.club_ank
	
			LEFT JOIN base b
				ON b.dt_shift=date(substr(sh.dt_shift, 1, 10))
				AND b.club=sh.club
				
			GROUP BY 	ns.login,  
						date(substr(sh.dt_shift, 1, 10)),
						sh.club
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


# Google Sheets хранит только оперативную витрину. Полная история остаётся в SQLite
# и продолжает использоваться запросами union/shifts/shifts_ext/records.
_sheets_cutoff = "date('now', '+3 hours', '-3 months')"
_union_without_semicolon = union.strip().rstrip(';')
_shifts_ext_without_semicolon = shifts_ext.strip().rstrip(';')

sheets_union = f'''
SELECT dt_rep, s_name, kpi, fact
FROM ({_union_without_semicolon}) AS all_kpi
WHERE date(dt_rep) >= {_sheets_cutoff}
ORDER BY date(dt_rep), s_name, kpi;
'''

sheets_shifts_ext = f'''
SELECT shift_second_name, shift_first_name, dt_shift, club, dur
FROM ({_shifts_ext_without_semicolon}) AS all_shifts
WHERE date(substr(dt_shift, 1, 10)) >= {_sheets_cutoff}
ORDER BY date(substr(dt_shift, 1, 10)), shift_second_name, shift_first_name;
'''

sheets_shifts = f'''
SELECT
    DATE(substr(sh.dt_shift, 1, 10), 'start of month', '+1 month', '-1 day') AS last_day_of_month,
    ns.login AS s_name,
    SUM(sh.dur) AS cnt_h,
    SUM(ROUND(sh.dur / 6, 3)) AS total_cnt_smen
FROM shifts sh
JOIN users ns ON (
    sh.shift_login IS NOT NULL
    AND lower(sh.shift_login) = lower(ns.login)
) OR (
    sh.shift_login IS NULL
    AND sh.shift_second_name = ns.second_name
    AND sh.shift_first_name = ns.first_name
)
WHERE date(substr(sh.dt_shift, 1, 10)) >= {_sheets_cutoff}
GROUP BY last_day_of_month, ns.login
ORDER BY last_day_of_month, ns.login;
'''

_sheets_union_without_semicolon = sheets_union.strip().rstrip(';')
sheets_records = f'''
WITH recent_records AS (
    {_sheets_union_without_semicolon}
)
SELECT s_name, kpi, SUM(fact) AS total
FROM recent_records
GROUP BY s_name, kpi
ORDER BY kpi, total DESC, s_name;
'''
