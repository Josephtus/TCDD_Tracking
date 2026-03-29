SET @db = 'tcdd_bot_db';

-- users.full_name
SET @col = 'full_name';
SET @tbl = 'users';
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=@db AND TABLE_NAME=@tbl AND COLUMN_NAME=@col) = 0,
  CONCAT('ALTER TABLE ',@tbl,' ADD COLUMN full_name VARCHAR(150) NULL AFTER username'),
  'SELECT ''full_name already exists'' AS info'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- users.status
SET @col = 'status';
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=@db AND TABLE_NAME=@tbl AND COLUMN_NAME=@col) = 0,
  'ALTER TABLE users ADD COLUMN status ENUM(''pending'',''approved'',''rejected'',''blocked'') NOT NULL DEFAULT ''pending'' AFTER full_name',
  'SELECT ''status already exists'' AS info'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- users.is_admin
SET @col = 'is_admin';
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=@db AND TABLE_NAME=@tbl AND COLUMN_NAME=@col) = 0,
  'ALTER TABLE users ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0 AFTER status',
  'SELECT ''is_admin already exists'' AS info'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- tasks.user_id
SET @col = 'user_id';
SET @tbl = 'tasks';
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=@db AND TABLE_NAME=@tbl AND COLUMN_NAME=@col) = 0,
  'ALTER TABLE tasks ADD COLUMN user_id INT NULL AFTER id',
  'SELECT ''user_id already exists'' AS info'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Mevcut admin kullanıcıyı approved yap
UPDATE users SET status='approved', is_admin=1 WHERE telegram_id='8608401086';

SELECT 'MIGRATION COMPLETED' AS result;
