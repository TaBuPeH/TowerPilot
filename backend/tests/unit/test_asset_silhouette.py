import cv2
import numpy as np

from player.asset_verify import locate_silhouette


def sprite():
    image=np.zeros((80,80,4),dtype=np.uint8)
    cv2.fillPoly(image,[np.array([[40,2],[78,70],[2,70]])],(255,255,255,255))
    cv2.circle(image,(40,45),10,(0,0,0,0),-1)
    return image


def test_flat_asset_with_internal_hole_matches_native_render():
    source=sprite();frame=np.zeros((250,250,3),dtype=np.uint8)
    frame[80:160,50:130]=source[:,:,:3]
    hit=locate_silhouette(source,frame,[20,60,160,140])
    assert hit and hit['silhouette_score']>.98


def test_wrong_hole_or_duplicate_is_not_a_match():
    source=sprite();frame=np.zeros((250,250,3),dtype=np.uint8)
    frame[80:160,20:100]=source[:,:,:3]
    frame[80:160,140:220]=source[:,:,:3]
    assert locate_silhouette(source,frame,[0,0,250,250]) is None
    frame[:]=0;frame[80:160,50:130]=source[:,:,:3]
    cv2.circle(frame,(90,125),10,(255,255,255),-1)
    assert locate_silhouette(source,frame,[20,60,160,140]) is None


def test_white_glyph_ignores_colored_sprite_backplate():
    source=np.full((80,80,4),(0,240,150,255),dtype=np.uint8)
    cv2.line(source,(12,12),(68,68),(255,255,255,255),12)
    cv2.line(source,(68,12),(12,68),(255,255,255,255),12)
    frame=np.zeros((180,180,3),dtype=np.uint8)
    white=source[:,:,:3].min(axis=2)>205
    frame[40:120,50:130][white]=255
    hit=locate_silhouette(source,frame,[20,20,140,140],white=True)
    assert hit and hit['silhouette_score']>.98


def test_repeated_manifest_art_can_match_without_relaxing_default_uniqueness():
    source=sprite();frame=np.zeros((250,250,3),dtype=np.uint8)
    frame[80:160,20:100]=source[:,:,:3]
    frame[80:160,140:220]=source[:,:,:3]
    assert locate_silhouette(source,frame,[0,0,250,250]) is None
    hit=locate_silhouette(source,frame,[0,0,250,250],allow_multiple=True)
    assert hit and hit['silhouette_score']>.98
