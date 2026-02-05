<script lang="ts">
  import { Toggle as TogglePrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';
  import { toggleVariants, type ToggleVariants } from './variants.js';

  type Props = TogglePrimitive.RootProps &
    ToggleVariants & {
      class?: string;
    };

  let { class: className, variant = 'default', size = 'default', children, ...restProps }: Props = $props();
</script>

<TogglePrimitive.Root
  class={cn(toggleVariants({ variant, size }), className)}
  {children}
  {...restProps}
/>
