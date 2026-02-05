<script lang="ts">
  import { LinkPreview as HoverCardPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = HoverCardPrimitive.ContentProps & {
    class?: string;
    align?: 'start' | 'center' | 'end';
    sideOffset?: number;
  };

  let {
    class: className,
    align = 'center',
    sideOffset = 4,
    children,
    ...restProps
  }: Props = $props();
</script>

<HoverCardPrimitive.Content
  {align}
  {sideOffset}
  class={cn(
    'z-50 w-64 rounded-md border bg-popover p-4 text-popover-foreground shadow-md outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</HoverCardPrimitive.Content>
