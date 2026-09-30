#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>

namespace apollo::simulation::ml {
struct ReferencePoint { double x, y, left, right; };
struct ReferenceSample { double x, y, heading, curvature, left, right; };
// Cubic Hermite interpolation joins the actual routing lane centerlines.
// Coordinates stay float64, including large ENU map origins.
class ReferenceLine {
 public:
  void Set(std::vector<ReferencePoint> points) {
    road_.clear();
    for(size_t i=1;i<points.size();++i) {
      const auto& a=points[i-1];const auto& b=points[i];
      const double dx=b.x-a.x,dy=b.y-a.y,n=std::hypot(dx,dy);
      if(n>1e-6) road_.push_back({a.x,a.y,dx/n,dy/n,n,a.left,a.right,
                                (b.left-a.left)/n,(b.right-a.right)/n});
    }
    points_.clear(); arc_.clear();
    for (const auto& p : points) {
      if (!points_.empty() && std::hypot(p.x-points_.back().x, p.y-points_.back().y)<1e-5) continue;
      arc_.push_back(points_.empty() ? 0 : arc_.back()+std::hypot(p.x-points_.back().x,p.y-points_.back().y));
      points_.push_back(p);
    }
    if (points_.size()<3) throw std::runtime_error("Routing reference has fewer than three points");
    // Lane joins can leave millimetre-long segments among 10 cm samples. A
    // point-weighted smoother followed by Hermite differentiation amplifies
    // those spacing changes into artificial curvature spikes. Resample in
    // metric arc distance before smoothing, preserving the actual endpoints.
    const auto input=points_;
    const auto input_arc=arc_;
    points_.clear();arc_.clear();
    for(double s=0;s<input_arc.back();s+=.1) {
      const size_t i=std::min(static_cast<size_t>(std::upper_bound(input_arc.begin(),input_arc.end(),s)-input_arc.begin()-1),input.size()-2);
      const double t=(s-input_arc[i])/(input_arc[i+1]-input_arc[i]);
      const auto& a=input[i];const auto& b=input[i+1];
      points_.push_back({a.x+t*(b.x-a.x),a.y+t*(b.y-a.y),a.left+t*(b.left-a.left),a.right+t*(b.right-a.right)});
      arc_.push_back(s);
    }
    if(input_arc.back()-arc_.back()<.02) {points_.back()=input.back();arc_.back()=input_arc.back();}
    else {points_.push_back(input.back());arc_.push_back(input_arc.back());}
    if(points_.size()<3) throw std::runtime_error("Routing reference shorter than sampling resolution");
    // Smooth polyline heading discontinuities in metric arc distance before
    // differentiating: sigma 0.25 m, radius 0.75 m. Keep map endpoints fixed.
    const auto raw=points_;
    for(size_t i=0;i<points_.size();++i) {
      double x=0,y=0,weight=0;
      const auto begin=std::lower_bound(arc_.begin(),arc_.end(),arc_[i]-.75)-arc_.begin();
      for(size_t j=begin;j<points_.size() && arc_[j]<=arc_[i]+.75;++j) {
        const double ds=arc_[j]-arc_[i],w=std::exp(-ds*ds/.125);
        x+=w*(raw[j].x-raw[i].x);y+=w*(raw[j].y-raw[i].y);weight+=w;
      }
      // Extrapolate endpoint tangents for the smoothing kernel. Truncating the
      // kernel but pinning endpoints otherwise creates artificial tight bends
      // near a route start/destination, even on a straight final lane.
      for(double extra=.1;extra<=.751;extra+=.1) {
        for(bool front:{true,false}) {
          const size_t a=front?0:raw.size()-1,b=front?1:raw.size()-2;
          const double sample_s=front?-extra:arc_.back()+extra;
          const double ds=sample_s-arc_[i];
          if(std::abs(ds)>.75) continue;
          const double scale=extra/std::abs(arc_[b]-arc_[a]);
          const double gx=raw[a].x+(raw[a].x-raw[b].x)*scale;
          const double gy=raw[a].y+(raw[a].y-raw[b].y)*scale;
          const double w=std::exp(-ds*ds/.125);
          x+=w*(gx-raw[i].x);y+=w*(gy-raw[i].y);weight+=w;
        }
      }
      points_[i].x+=x/weight;points_[i].y+=y/weight;
    }
    // Restore endpoints with a smooth affine correction, not a discontinuous
    // pinned sample that would reintroduce a curvature spike next to the goal.
    const double start_x=raw.front().x-points_.front().x,start_y=raw.front().y-points_.front().y;
    const double end_x=raw.back().x-points_.back().x,end_y=raw.back().y-points_.back().y;
    for(size_t i=0;i<points_.size();++i) {
      const double t=arc_[i]/arc_.back();
      points_[i].x+=(1-t)*start_x+t*end_x;
      points_[i].y+=(1-t)*start_y+t*end_y;
    }
    for(size_t i=1;i<points_.size();++i)
      arc_[i]=arc_[i-1]+std::hypot(points_[i].x-points_[i-1].x,points_[i].y-points_[i-1].y);
    dx_.resize(points_.size()); dy_.resize(points_.size());
    for (size_t i=0;i<points_.size();++i) {
      const size_t a=i ? i-1 : i, b=std::min(i+1,points_.size()-1);
      const double ds=arc_[b]-arc_[a];
      dx_[i]=(points_[b].x-points_[a].x)/ds;
      dy_[i]=(points_[b].y-points_[a].y)/ds;
    }
  }
  // Road limits belong to the original map, not the smoothed driving
  // reference. Smoothing a bend must never move its physical boundaries.
  bool ContainsRoad(double x,double y,double margin) const {
    double best=std::numeric_limits<double>::infinity(),l=0,left=0,right=0;
    for(const auto& a:road_) {
      const double px=x-a.x,py=y-a.y;
      const double t=std::clamp(px*a.nx+py*a.ny,0.,a.length);
      const double ex=px-t*a.nx,ey=py-t*a.ny,d=ex*ex+ey*ey;
      if(d<best) {best=d;l=-ex*a.ny+ey*a.nx;
        left=a.left+t*a.dleft;right=a.right+t*a.dright;}
    }
    return std::isfinite(best) && l+margin<=left && l-margin>=-right;
  }
  double length() const { return arc_.back(); }
  double MinimumWidth(double begin,double ahead) const {
    double width=std::numeric_limits<double>::infinity();
    for(double s=begin;s<=std::min(length(),begin+ahead);s+=.25) {
      const auto r=At(s);width=std::min(width,r.left+r.right);
    }
    return width;
  }
  bool HasCurve(double begin,double ahead=3.,double threshold=.03) const {
    for(double s=begin;s<=std::min(length(),begin+ahead);s+=.25)
      if(std::abs(At(s).curvature)>threshold) return true;
    return false;
  }
  ReferenceSample At(double s) const {
    s=std::clamp(s,0.,length());
    const size_t i=std::min(static_cast<size_t>(std::upper_bound(arc_.begin(),arc_.end(),s)-arc_.begin()-1),points_.size()-2);
    const double h=arc_[i+1]-arc_[i], t=(s-arc_[i])/h;
    const auto& a=points_[i]; const auto& b=points_[i+1];
    auto axis=[&](double delta,double da,double db) {
      const double c=3*delta-h*(2*da+db), d=-2*delta+h*(da+db);
      return std::vector<double>{h*da*t+c*t*t+d*t*t*t, da+(2*c*t+3*d*t*t)/h, (2*c+6*d*t)/(h*h)};
    };
    const auto x=axis(b.x-a.x,dx_[i],dx_[i+1]), y=axis(b.y-a.y,dy_[i],dy_[i+1]);
    const double norm=std::hypot(x[1],y[1]);
    return {a.x+x[0],a.y+y[0],std::atan2(y[1],x[1]),
        (x[1]*y[2]-y[1]*x[2])/std::pow(norm,3),a.left+t*(b.left-a.left),a.right+t*(b.right-a.right)};
  }
  bool Project(double x,double y,double* s,double* l,double begin=0,double end=std::numeric_limits<double>::infinity()) const {
    double best=std::numeric_limits<double>::infinity(), bs=0;
    for (size_t i=0;i+1<points_.size();++i) {
      if (arc_[i+1]<begin || arc_[i]>end) continue;
      const double dx=points_[i+1].x-points_[i].x, dy=points_[i+1].y-points_[i].y;
      const double t=std::clamp(((x-points_[i].x)*dx+(y-points_[i].y)*dy)/(dx*dx+dy*dy),0.,1.);
      const double d=std::hypot(x-points_[i].x-t*dx,y-points_[i].y-t*dy);
      if(d<best) {best=d;bs=arc_[i]+t*(arc_[i+1]-arc_[i]);}
    }
    if(!std::isfinite(best)) return false;
    for(int i=0;i<3;++i) {
      const auto p=At(bs);
      bs=std::clamp(bs+(x-p.x)*std::cos(p.heading)+(y-p.y)*std::sin(p.heading),std::max(0.,begin),std::min(length(),end));
    }
    const auto p=At(bs); *s=bs; *l=-(x-p.x)*std::sin(p.heading)+(y-p.y)*std::cos(p.heading);
    return true;
  }
 private:
  struct RoadSegment {double x,y,nx,ny,length,left,right,dleft,dright;};
  std::vector<RoadSegment> road_;
  std::vector<ReferencePoint> points_;
  std::vector<double> arc_, dx_, dy_;
};
}  // namespace apollo::simulation::ml
