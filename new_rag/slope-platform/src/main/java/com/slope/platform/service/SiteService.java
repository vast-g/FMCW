package com.slope.platform.service;

import com.slope.platform.common.BusinessException;
import com.slope.platform.dto.SiteRequest;
import com.slope.platform.entity.Site;
import com.slope.platform.repository.DeviceRepository;
import com.slope.platform.repository.SiteRepository;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 点位业务。
 *
 * <p>类级 {@code readOnly = true}，写方法单独标注：这样"不小心在查询里改了数据"会直接报错，
 * 而不是悄悄多一次 UPDATE。同时只读事务能让数据库少做一部分工作。
 */
@Service
@Transactional(readOnly = true)
public class SiteService {

    private final SiteRepository siteRepository;
    private final DeviceRepository deviceRepository;

    public SiteService(SiteRepository siteRepository, DeviceRepository deviceRepository) {
        this.siteRepository = siteRepository;
        this.deviceRepository = deviceRepository;
    }

    /** 列表，可按边坡分组过滤（同一条边坡上的点位要合起来看趋势）。 */
    public List<Site> list(String slopeGroup) {
        return (slopeGroup == null || slopeGroup.isBlank())
                ? siteRepository.findAllByOrderByIdAsc()
                : siteRepository.findBySlopeGroupOrderByIdAsc(slopeGroup.trim());
    }

    public Site get(Integer id) {
        return siteRepository.findById(id)
                .orElseThrow(() -> BusinessException.notFound("点位不存在: id=" + id));
    }

    @Transactional
    public Site create(SiteRequest request) {
        if (siteRepository.existsByCode(request.code())) {
            throw BusinessException.conflict("点位编号已存在: " + request.code());
        }
        Site site = new Site();
        apply(site, request);
        return siteRepository.save(site);
    }

    @Transactional
    public Site update(Integer id, SiteRequest request) {
        Site site = get(id);
        if (siteRepository.existsByCodeAndIdNot(request.code(), id)) {
            throw BusinessException.conflict("点位编号已被其它点位占用: " + request.code());
        }
        apply(site, request);
        return siteRepository.save(site);
    }

    /**
     * 删除点位。
     *
     * <p>先看有没有设备挂在这个点位上。有则返回 409 并<b>告诉用户还有几台</b>——
     * "删除失败"这种提示在现场毫无价值，人还得自己去翻设备列表。
     *
     * <p>至于读数和告警事件的外键，不在这里逐张表检查（那会让 Service 依赖越来越多表），
     * 交给数据库的外键约束兜底，由全局异常处理器转成 409。
     * Python 侧其实踩过同一个坑（见 slope_monitor.seed_if_empty 的注释），
     * 这里把它变成一句能看懂的提示。
     */
    @Transactional
    public void delete(Integer id) {
        Site site = get(id);
        long devices = deviceRepository.countBySiteId(id);
        if (devices > 0) {
            throw BusinessException.conflict(
                    "点位 %s 下还有 %d 台设备，请先删除或转移这些设备".formatted(site.getCode(), devices));
        }
        siteRepository.delete(site);
        // 立即刷到数据库：让外键冲突在业务方法内抛出，而不是等到事务提交时才冒出
        siteRepository.flush();
    }

    private void apply(Site site, SiteRequest request) {
        site.setCode(request.code().trim());
        site.setName(request.name().trim());
        site.setDescription(request.description());
        site.setSlopeGroup(request.slopeGroup().trim());
        if (request.lat() != null) {
            site.setLat(request.lat());
        }
        if (request.lng() != null) {
            site.setLng(request.lng());
        }
        if (request.isPrimary() != null) {
            site.setIsPrimary(request.isPrimary());
        }
    }
}
