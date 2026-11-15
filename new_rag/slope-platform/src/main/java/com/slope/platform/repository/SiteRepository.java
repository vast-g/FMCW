package com.slope.platform.repository;

import com.slope.platform.entity.Site;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * 点位仓储。
 *
 * <p>过滤用两个方法而不是一个"参数可为空"的 @Query：点位只有几十条，
 * 代码路径越直白越好，避免以后再猜这个 null 到底代表"不过滤"还是"查空分组"。
 */
public interface SiteRepository extends JpaRepository<Site, Integer> {

    List<Site> findAllByOrderByIdAsc();

    List<Site> findBySlopeGroupOrderByIdAsc(String slopeGroup);

    /** 编号是对外的业务唯一键，新增/修改前都要查重，避免靠数据库报错兜底（错误信息对用户不友好）。 */
    boolean existsByCode(String code);

    /** 修改时排除自己，否则"只改名字不改编号"会被自己判成重复。 */
    boolean existsByCodeAndIdNot(String code, Integer id);
}
