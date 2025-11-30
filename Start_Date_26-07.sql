-- SQLite
DROP TABLE IF EXISTS Players;

CREATE TABLE
Players(
    Id text PRIMARY KEY,
    Name text NOT NULL,
    GroupNumber int NOT NULL,
    Active boolean NOT NULL,
    Rating int,
    Type text,
    Last_Update datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO Players (Id, Name, GroupNumber, Active, Rating, Type)
VALUES 
(1,'A. Veld','2',true,1454,'Youth'),
(2,'B. Ahlers','1',true,2092,'Senior'),
(3,'B. Albertus','1',true,2071,'Senior'),
(4,'C. Remie','2',true,NULL,'Senior'),
(5,'C. Schoon','2',true,1603,'Senior'),
(6,'C. Veld','2',true,NULL,'Senior'),
(7,'D. Bhoelan','2',true,NULL,'Senior'),
(8,'D. Brinkman','2',true,1700,'Senior'),
(9,'D. Kanbier','1',true,1778,'Senior'),
(10,'D. Ramadan','2',true,1819,'Senior'),
(11,'D. Strikwerda','2',true,1200,'Senior'),
(12,'D. van Nies','2',true,1734,'Senior'),
(13,'E. Guijt','2',true,1906,'Senior'),
(14,'F. Martens','2',true,1715,'Senior'),
(15,'G. Eggink','2',true,1886,'Senior'),
(16,'H. Minnema','1',true,1815,'Senior'),
(17,'H. Noordhoek','1',true,2001,'Senior'),
(18,'H. Ouwens','1',true,1956,'Senior'),
(19,'J. Baas','1',true,1976,'Senior'),
(20,'J. Blankespoor','1',true,1922,'Senior'),
(21,'J. de Roo','2',true,1615,'Senior'),
(22,'J. Koster','1',true,1928,'Senior'),
(23,'J. Lipka','2',true,1656,'Senior'),
(24,'J. Mostert','1',true,1894,'Senior'),
(25,'J. Sibbing','1',true,1906,'Senior'),
(26,'J. Tan','1',true,1858,'Senior'),
(27,'J. van den Berg','1',true,1848,'Senior'),
(28,'J. Veldhuizen','1',true,1767,'Senior'),
(29,'J.W. Duijzer','1',true,2124,'Senior'),
(30,'K. Breed','2',true,NULL,'Senior'),
(31,'M. Engelsman','2',true,NULL,'Senior'),
(32,'M. Hettfleisch','1',true,1891,'Senior'),
(33,'M. Nepveu','1',true,1968,'Senior'),
(34,'M. Wichhart','1',true,1936,'Senior'),
(35,'N. Peerdeman','1',true,1944,'Senior'),
(36,'O. van den Bout','2',true,1749,'Youth'),
(37,'P. Hijma','2',true,1650,'Senior'),
(38,'P. van der Werve','1',true,2062,'Senior'),
(39,'R. de Vries','2',true,1639,'Senior'),
(40,'R. Jansen','2',true,1533,'Youth'),
(41,'R. Matai','2',true,1748,'Senior'),
(42,'R. Minnema','1',true,1910,'Senior'),
(43,'S. de Swart','1',true,1995,'Senior'),
(44,'S. de Vries','2',true,1741,'Youth'),
(45,'S. Kooiman','1',true,1874,'Senior'),
(46,'T. Morcus','1',true,1906,'Senior'),
(47,'T. vd Nieuwendijk','2',true,1761,'Senior'),
(48,'Tim van der Helm','2',true,NULL,'Youth'),
(49,'Tom van der Helm','2',true,NULL,'Senior'),
(50,'H. Boerkamp','1',true,1900,'Senior'),
(51,'B. Bannink','1',true,2100,'Senior'),
(52,'K. van der Heijden','2',true,1800,'Youth'),
(53,'F. van Bolhuis','2',true,1500,'Youth'),
(54,'D. Jermin','2',true,NULL,'Youth')
;


